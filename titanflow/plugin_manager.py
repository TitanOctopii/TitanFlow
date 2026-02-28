"""TitanFlow Plugin Manager — runtime discovery, loading, and execution.

Internal module — plugins should NOT import from here.
Handles:
    - Scanning plugin directories for manifest.json files
    - Loading and validating plugins
    - Building tool descriptions for the LLM system prompt
    - Executing tool calls from LLM responses
    - Hot-reload support (future)
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
from pathlib import Path
from typing import Any, TYPE_CHECKING

from titanflow.plugin_sdk import PluginContext, ToolPlugin, ModulePlugin, HookPlugin

if TYPE_CHECKING:
    from titanflow.core.engine import TitanFlowEngine

logger = logging.getLogger("titanflow.plugins")


class PluginManager:
    """Discovers, loads, and manages TitanFlow plugins.

    Usage:
        pm = PluginManager(engine)
        pm.discover()
        await pm.load_all()
        # Tools are now available via pm.get_tool(), pm.tool_descriptions(), etc.
    """

    def __init__(self, engine: TitanFlowEngine) -> None:
        self.engine = engine
        self._plugin_dirs: list[str] = []
        self._manifests: list[dict] = []
        self._tools: dict[str, ToolPlugin] = {}
        self._tool_contexts: dict[str, PluginContext] = {}  # keyed by tool_name
        self._modules: dict[str, ModulePlugin] = {}
        self._hooks: dict[str, list[HookPlugin]] = {}
        self._contexts: dict[str, PluginContext] = {}  # keyed by plugin_id

        # Build plugin directories from config
        config = engine.config
        # Default dirs: ~/.titanflow/plugins/ + instance-specific
        home_plugins = os.path.expanduser("~/.titanflow/plugins")
        self._plugin_dirs.append(home_plugins)

        # Config-driven plugin dirs (from YAML)
        plugin_config = getattr(config.modules, "plugins", None)
        if plugin_config and hasattr(plugin_config, "dirs"):
            for d in plugin_config.dirs:
                expanded = os.path.expanduser(d)
                if expanded not in self._plugin_dirs:
                    self._plugin_dirs.append(expanded)

        # Enabled/disabled list from config
        self._enabled_list: list[str] | None = None
        self._plugin_configs: dict[str, dict] = {}
        if plugin_config:
            # enabled_plugins is the optional whitelist of plugin IDs
            if hasattr(plugin_config, "enabled_plugins") and plugin_config.enabled_plugins is not None:
                self._enabled_list = plugin_config.enabled_plugins
            if hasattr(plugin_config, "config") and plugin_config.config:
                self._plugin_configs = plugin_config.config

    def discover(self) -> list[dict]:
        """Scan plugin directories for manifest.json files."""
        self._manifests = []
        for dir_path in self._plugin_dirs:
            p = Path(dir_path)
            if not p.exists():
                logger.debug("Plugin dir does not exist: %s", dir_path)
                continue
            for manifest_path in sorted(p.glob("*/manifest.json")):
                try:
                    with open(manifest_path) as f:
                        manifest = json.load(f)
                    manifest["_path"] = str(manifest_path.parent)
                    manifest["_manifest_path"] = str(manifest_path)

                    plugin_id = manifest.get("id", manifest_path.parent.name)
                    manifest.setdefault("id", plugin_id)

                    # Check enabled list
                    if self._enabled_list is not None and plugin_id not in self._enabled_list:
                        logger.info("Plugin skipped (not in enabled list): %s", plugin_id)
                        continue

                    self._manifests.append(manifest)
                    logger.info("Discovered plugin: %s (%s)", plugin_id, manifest.get("type", "unknown"))
                except Exception:
                    logger.warning("Failed to read manifest: %s", manifest_path, exc_info=True)

        logger.info("Plugin discovery complete: %d plugin(s) found", len(self._manifests))
        return self._manifests

    async def load_all(self) -> None:
        """Load all discovered plugins."""
        for manifest in self._manifests:
            try:
                await self._load_plugin(manifest)
            except Exception:
                logger.error("Failed to load plugin: %s", manifest.get("id", "?"), exc_info=True)

        logger.info(
            "Plugins loaded: %d tool(s), %d module(s), %d hook event(s)",
            len(self._tools),
            len(self._modules),
            len(self._hooks),
        )

    async def _load_plugin(self, manifest: dict) -> None:
        """Load a single plugin from its manifest."""
        plugin_id = manifest["id"]
        plugin_dir = manifest["_path"]
        entry = manifest.get("entry", "plugin.py")
        module_path = os.path.join(plugin_dir, entry)

        if not os.path.exists(module_path):
            raise FileNotFoundError(f"Plugin entry point not found: {module_path}")

        # Import the plugin module
        spec = importlib.util.spec_from_file_location(
            f"titanflow_plugin_{plugin_id}",
            module_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Convention: module exports a `Plugin` class or `plugin` instance
        if hasattr(module, "Plugin"):
            instance = module.Plugin()
        elif hasattr(module, "plugin"):
            instance = module.plugin
        else:
            raise ValueError(f"Plugin {plugin_id} has no Plugin class or plugin instance")

        # Build plugin context
        plugin_config = {}
        # Merge manifest config_schema defaults
        if "config_schema" in manifest:
            for key, schema in manifest["config_schema"].items():
                if "default" in schema:
                    plugin_config[key] = schema["default"]
        # Override with YAML config
        if plugin_id in self._plugin_configs:
            plugin_config.update(self._plugin_configs[plugin_id])

        ctx = PluginContext(
            instance_name=self.engine.config.name,
            config=plugin_config,
            send_message=self._make_send_message(),
            llm_chat=self._make_llm_chat(),
            logger=logging.getLogger(f"titanflow.plugin.{plugin_id}"),
        )
        self._contexts[plugin_id] = ctx

        # Register by type
        plugin_type = manifest.get("type", "tool")

        if plugin_type == "tool" and isinstance(instance, ToolPlugin):
            tool_name = instance.name()
            self._tools[tool_name] = instance
            self._tool_contexts[tool_name] = ctx
            logger.info("Registered tool: %s (from %s)", tool_name, plugin_id)

        elif plugin_type == "module" and isinstance(instance, ModulePlugin):
            self._modules[plugin_id] = instance
            await instance.start(ctx)
            logger.info("Started module plugin: %s", plugin_id)

        elif plugin_type == "hook" and isinstance(instance, HookPlugin):
            event_name = instance.event()
            self._hooks.setdefault(event_name, []).append(instance)
            logger.info("Registered hook: %s on event '%s'", plugin_id, event_name)

        else:
            # Try to auto-detect type from class
            if isinstance(instance, ToolPlugin):
                tool_name = instance.name()
                self._tools[tool_name] = instance
                self._tool_contexts[tool_name] = ctx
                logger.info("Registered tool (auto-detected): %s", tool_name)
            elif isinstance(instance, ModulePlugin):
                self._modules[plugin_id] = instance
                await instance.start(ctx)
                logger.info("Started module (auto-detected): %s", plugin_id)
            elif isinstance(instance, HookPlugin):
                event_name = instance.event()
                self._hooks.setdefault(event_name, []).append(instance)
                logger.info("Registered hook (auto-detected): %s", plugin_id)
            else:
                logger.warning("Plugin %s has unknown type: %s", plugin_id, type(instance).__name__)

    def _make_send_message(self):
        """Create a send_message callable for plugin context."""
        async def _send(chat_id: str, text: str) -> None:
            # Plugins can send messages via the engine's event bus
            await self.engine.events.emit(
                "plugin.send_message",
                chat_id=chat_id,
                text=text,
                source="plugin",
            )
        return _send

    def _make_llm_chat(self):
        """Create an llm_chat callable for plugin context."""
        async def _chat(messages: list[dict[str, str]]) -> str:
            return await self.engine.llm.chat(messages=messages, temperature=0.7)
        return _chat

    # ─── Tool Interface (used by bot.py) ─────────────────────

    @property
    def available_tools(self) -> dict[str, ToolPlugin]:
        """All loaded tool plugins."""
        return dict(self._tools)

    def get_tool(self, name: str) -> ToolPlugin | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def tool_descriptions(self) -> str:
        """Build a tool description block for the LLM system prompt.

        Uses CALL_TOOL format — tested to work on lfm2:24b, cogito, qwen, gemma.
        The JSON-only "output ONLY a JSON object" format silences lfm2:24b entirely.

        Format:
            CALL_TOOL shell_exec ls ~/Projects
            CALL_TOOL shell_exec {"command": "git status"}
            CALL_TOOL file_write {"path": "/tmp/x.py", "content": "print(1)"}
        """
        if not self._tools:
            return ""

        tool_lines = []
        for name, tool in self._tools.items():
            params = tool.parameters()
            props = params.get("properties", {})
            required = params.get("required", [])
            param_parts = []
            for pname, pschema in props.items():
                req = "*" if pname in required else ""
                desc = pschema.get("description", "")
                param_parts.append(f"    {pname}{req}: {desc}")
            tool_lines.append(f"  {name} — {tool.description()}")
            tool_lines.extend(param_parts)

        tools_block = "\n".join(tool_lines)

        return (
            "\n\n## Tools\n"
            "When you need to run a command or write a file, include a CALL_TOOL line in your response.\n"
            "Format: CALL_TOOL <tool_name> <command or JSON params>\n"
            "Examples:\n"
            "  CALL_TOOL shell_exec ls ~/Projects\n"
            '  CALL_TOOL shell_exec {"command": "git log --oneline -5"}\n'
            '  CALL_TOOL file_write {"path": "/tmp/hello.py", "content": "print(\'hello\')"}\n'
            "You can write normal text before or after the CALL_TOOL line.\n"
            "Only use a tool when you actually need to run something — not for general conversation.\n\n"
            f"Available tools:\n{tools_block}"
        )

    async def execute_tool(self, tool_name: str, params: dict) -> str:
        """Execute a tool by name and return the result string."""
        tool = self._tools.get(tool_name)
        if not tool:
            return f"⚠ Unknown tool: {tool_name}"

        ctx = self._tool_contexts.get(tool_name)
        if ctx is None:
            # Fallback: create a minimal context
            ctx = PluginContext(
                instance_name=self.engine.config.name,
                config={},
                send_message=self._make_send_message(),
                llm_chat=self._make_llm_chat(),
                logger=logging.getLogger(f"titanflow.plugin.{tool_name}"),
            )

        try:
            result = await tool.execute(ctx, params)
            logger.info("Tool executed: %s → %d chars", tool_name, len(result))
            return result
        except Exception as e:
            logger.error("Tool execution failed: %s — %s", tool_name, e, exc_info=True)
            return f"⚠ Tool error: {e}"

    # ─── Hook Interface ──────────────────────────────────────

    async def fire_hook(self, event: str, data: dict) -> dict | None:
        """Fire hooks for an event. Returns modified data or None if suppressed."""
        hooks = self._hooks.get(event, [])
        for hook in hooks:
            try:
                # Find context for this hook
                ctx = PluginContext(
                    instance_name=self.engine.config.name,
                    config={},
                    send_message=self._make_send_message(),
                    llm_chat=self._make_llm_chat(),
                    logger=logging.getLogger("titanflow.plugin.hook"),
                )
                result = await hook.handle(ctx, data)
                if result is None:
                    return None  # Event suppressed
                data = result
            except Exception:
                logger.error("Hook error on event '%s'", event, exc_info=True)
        return data

    # ─── Shutdown ─────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Stop all module plugins and clean up."""
        for pid, module in self._modules.items():
            try:
                await module.stop()
                logger.info("Stopped module plugin: %s", pid)
            except Exception:
                logger.error("Error stopping module plugin: %s", pid, exc_info=True)

    # ─── Status ──────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Get plugin system status."""
        return {
            "plugin_dirs": self._plugin_dirs,
            "discovered": len(self._manifests),
            "tools": list(self._tools.keys()),
            "modules": list(self._modules.keys()),
            "hooks": {event: len(hooks) for event, hooks in self._hooks.items()},
        }
