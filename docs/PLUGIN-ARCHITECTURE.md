# TitanFlow Plugin Architecture — Design Doc
## Date: 2026-02-28 · Author: CC (Gatekeeper)

---

## Goal

Give both Jr. Devs (Flow + Ollie) the ability to self-extend via a plugin system. Enable the TitanSafe marketplace vision. Check the "plugin system" graduation box.

---

## Principles

1. **Python-native.** No TypeScript, no Node. Pure Python packages.
2. **Same pattern for both sons.** One architecture, two instances.
3. **Simple first.** Local file-based plugins before hosted marketplace.
4. **SDK/Runtime split.** Stable SDK for plugin authors, internal runtime for execution.
5. **Hot-loadable.** Add a plugin without restarting TitanFlow.

---

## Plugin Types

| Type | Purpose | Example |
|------|---------|---------|
| **Tool** | Add a capability the LLM can invoke | Shell exec, web search, file writer |
| **Module** | Background service with scheduler | Research feeds, newspaper publisher |
| **Channel** | New chat platform adapter | Matrix, Signal, voice |
| **Hook** | Event interceptor | Message filter, audit logger, memory |
| **Skill** | Reusable prompt+tool bundle | "Code reviewer", "Deploy assistant" |

---

## Directory Structure

```
~/.titanflow/plugins/
├── shell-exec/
│   ├── manifest.json        # Plugin metadata
│   ├── plugin.py            # Entry point
│   └── README.md            # Documentation
├── web-search/
│   ├── manifest.json
│   └── plugin.py
└── ...
```

Project-level plugins:
```
/opt/titanflow/plugins/       # Instance-specific
```

---

## Manifest Schema

```json
{
  "id": "shell-exec",
  "name": "Shell Execution",
  "version": "0.1.0",
  "type": "tool",
  "description": "Execute shell commands with approval gates",
  "author": "TitanArray",
  "entry": "plugin.py",
  "requires": {
    "titanflow": ">=0.2.0",
    "python": ">=3.12"
  },
  "config_schema": {
    "security_mode": {"type": "string", "enum": ["deny", "allowlist", "full"], "default": "allowlist"},
    "allowed_commands": {"type": "array", "items": {"type": "string"}, "default": ["ls", "cat", "git"]}
  },
  "capabilities": ["exec", "background_jobs"],
  "tags": ["shell", "exec", "infrastructure"]
}
```

---

## Plugin SDK (Stable API)

```python
# titanflow/plugin_sdk.py

from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass

@dataclass
class PluginContext:
    """Injected at load time. Provides access to TitanFlow services."""
    instance_name: str          # "TitanFlow" or "TitanFlow-Ollie"
    config: dict[str, Any]      # Plugin-specific config from manifest
    send_message: callable      # async (chat_id, text) -> None
    llm_chat: callable          # async (messages) -> str
    mem0_recall: callable       # async (query) -> list[str]
    mem0_store: callable        # async (fact) -> bool
    logger: Any                 # logging.Logger

class ToolPlugin(ABC):
    """A tool the LLM can invoke during conversation."""

    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def parameters(self) -> dict: ...

    @abstractmethod
    async def execute(self, ctx: PluginContext, params: dict) -> str: ...

class ModulePlugin(ABC):
    """Background service with lifecycle."""

    @abstractmethod
    async def start(self, ctx: PluginContext) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

class HookPlugin(ABC):
    """Event interceptor."""

    @abstractmethod
    def event(self) -> str: ...  # "message:before", "message:after", "startup", "shutdown"

    @abstractmethod
    async def handle(self, ctx: PluginContext, data: dict) -> dict | None: ...
```

---

## Plugin Loader

```python
# titanflow/plugin_loader.py (runtime, internal)

import importlib.util
import json
import os
from pathlib import Path

class PluginLoader:
    """Discovers, validates, and loads plugins at runtime."""

    def __init__(self, plugin_dirs: list[str]):
        self.plugin_dirs = plugin_dirs
        self.loaded: dict[str, Any] = {}

    def discover(self) -> list[dict]:
        """Scan plugin directories for manifest.json files."""
        found = []
        for dir_path in self.plugin_dirs:
            p = Path(dir_path)
            if not p.exists():
                continue
            for manifest_path in p.glob("*/manifest.json"):
                with open(manifest_path) as f:
                    manifest = json.load(f)
                manifest["_path"] = str(manifest_path.parent)
                found.append(manifest)
        return found

    def load(self, manifest: dict, ctx) -> Any:
        """Load a plugin from its manifest and inject context."""
        plugin_dir = manifest["_path"]
        entry = manifest.get("entry", "plugin.py")
        module_path = os.path.join(plugin_dir, entry)

        spec = importlib.util.spec_from_file_location(
            f"titanflow_plugin_{manifest['id']}",
            module_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Convention: module exports a `plugin` variable or `Plugin` class
        if hasattr(module, "Plugin"):
            instance = module.Plugin()
        elif hasattr(module, "plugin"):
            instance = module.plugin
        else:
            raise ValueError(f"Plugin {manifest['id']} has no Plugin class or plugin instance")

        self.loaded[manifest["id"]] = instance
        return instance
```

---

## First Plugins (Ship Order)

### 1. shell-exec (MUST-HAVE — unblocks code production)

```python
# ~/.titanflow/plugins/shell-exec/plugin.py

import asyncio
import shlex
from titanflow.plugin_sdk import ToolPlugin, PluginContext

ALLOWLIST_DEFAULT = [
    "ls", "cat", "head", "tail", "grep", "find", "wc",
    "git", "python3", "pip", "npm", "node",
    "curl", "wget", "ssh", "scp",
    "systemctl", "journalctl", "docker",
]

class Plugin(ToolPlugin):
    def name(self) -> str:
        return "shell_exec"

    def description(self) -> str:
        return "Execute a shell command. Returns stdout/stderr. Commands outside the allowlist require Papa's approval."

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "cwd": {"type": "string", "description": "Working directory (optional)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"},
            },
            "required": ["command"]
        }

    async def execute(self, ctx: PluginContext, params: dict) -> str:
        command = params["command"]
        cwd = params.get("cwd", None)
        timeout = params.get("timeout", 30)

        # Check allowlist
        cmd_name = shlex.split(command)[0] if command else ""
        allowed = ctx.config.get("allowed_commands", ALLOWLIST_DEFAULT)

        if cmd_name not in allowed:
            return f"⚠ Command '{cmd_name}' not in allowlist. Ask Papa for approval."

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            output = stdout.decode()[:2000]
            errors = stderr.decode()[:500]
            result = f"Exit code: {proc.returncode}\n"
            if output:
                result += f"stdout:\n{output}\n"
            if errors:
                result += f"stderr:\n{errors}"
            return result.strip()
        except asyncio.TimeoutError:
            return f"⚠ Command timed out after {timeout}s"
        except Exception as e:
            return f"⚠ Exec error: {e}"
```

### 2. file-writer (MUST-HAVE — Jr. Devs need to create files)

Writes code to a file in the workspace directory. Combined with shell-exec for git operations.

### 3. memory-tools (nice-to-have — manual memory search/store)

Exposes mem0 recall/store as LLM-invokable tools. Already built in OpenClaw plugin, port to TF SDK.

---

## Integration with TitanFlow

### Bot.py Changes

The bot needs to:
1. Load plugins at startup
2. Include tool descriptions in the system prompt
3. Detect tool invocations in LLM responses
4. Execute tools and feed results back

This requires **function calling** support. Current approach: structured prompt that tells the LLM to output JSON when it wants to use a tool.

```
Available tools:
- shell_exec(command, cwd?, timeout?): Execute shell command
- file_write(path, content): Write content to a file

To use a tool, respond with ONLY a JSON object:
{"tool": "shell_exec", "params": {"command": "ls -la"}}

After I run the tool, I'll give you the result and you can continue.
```

### Config Addition (titanflow-ollie.yaml)

```yaml
  plugins:
    dirs:
      - "~/.titanflow/plugins"
    enabled:
      - shell-exec
      - file-writer
    config:
      shell-exec:
        security_mode: "allowlist"
        allowed_commands: ["ls", "cat", "git", "python3"]
```

---

## Marketplace (TitanSafe — Phase 2)

### Local Phase (now)
- Plugins in `~/.titanflow/plugins/`
- Manual install: git clone into plugins dir
- Discovery: scan manifest.json files

### GitHub Phase (next)
- Each plugin = GitHub repo with manifest.json
- Install: `titanflow plugin install github:kamaldatta76/tf-plugin-shell-exec`
- Update: `titanflow plugin update <name>`
- Registry: JSON file in TitanSafe repo listing all verified plugins

### Hosted Phase (future — titanarray.net/marketplace)
- Ghost page listing verified plugins
- Each plugin has: docs, install cmd, compatibility, stars
- Community submissions via GitHub PR to TitanSafe registry
- Pipeline tests each submission before "verified" badge

---

## Timeline

| Phase | What | Effort | Unlocks |
|-------|------|--------|---------|
| **Phase 1** (now) | SDK + loader + shell-exec + file-writer | 3 days | Code production ✅, Plugins ✅ |
| **Phase 2** (week 2) | Git-based install, 5+ plugins | 5 days | TitanSafe local |
| **Phase 3** (week 3+) | Hosted marketplace, community | 10+ days | TitanSafe public |

---

## Privacy
- No real names in any plugin code, manifests, or docs.
- Papa/Kid references only.
