# TitanFlow v0.3 — Release Delta
**Date:** 2026-02-28
**Source:** Sarge local state vs GitHub `main`
**Purpose:** Capture everything in v0.3 that hasn't landed in a commit. This doc is the v0.3 release commit spec.

---

## Summary

v0.3 adds four major pillars on top of v0.2:

1. **Plugin System** — runtime-discoverable tools, modules, and hooks via `PluginManager` + `PluginSDK`
2. **CALL_TOOL Protocol** — multi-model compatible tool invocation format that works on lfm2, cogito, qwen, gemma
3. **mem0 Long-Term Memory** — Qdrant + Ollama-based persistent memory with per-conversation recall and capture
4. **Bot hardening** — HTML parse mode, privacy guardrails, smarter grounding gate, `/new` context reset, `/plugins` command

---

## New Files (untracked on Sarge — must be added)

### `titanflow/plugin_manager.py`
Runtime discovery, loading, and execution of plugins.

**Key responsibilities:**
- Scans `~/.titanflow/plugins/` (and config-specified dirs) for `manifest.json` files
- Loads plugins via `importlib` — convention: `Plugin` class or `plugin` instance
- Routes by type: `ToolPlugin`, `ModulePlugin`, `HookPlugin`
- Builds `CALL_TOOL`-format tool descriptions for the LLM system prompt
- Executes tool calls from LLM responses, returns string results
- `shutdown()` stops all `ModulePlugin` instances cleanly

**Notable design decision:** Uses `CALL_TOOL` prefix format instead of JSON-only — JSON-only silences lfm2:24b entirely.

### `titanflow/plugin_sdk.py`
Stable public API for plugin authors. Plugin authors import only from here.

**Exports:**
- `PluginContext` — injected at load time; provides `instance_name`, `config`, `send_message`, `llm_chat`, `logger`, optional `mem0_recall`/`mem0_store`
- `ToolPlugin` (ABC) — `name()`, `description()`, `parameters()`, `execute(ctx, params) -> str`
- `ModulePlugin` (ABC) — `start(ctx)`, `stop()`
- `HookPlugin` (ABC) — `event()`, `handle(ctx, data) -> dict | None`

### `titanflow/core/mem0_client.py`
Lightweight mem0-style long-term memory using Qdrant + Ollama.

**Architecture:**
- Qdrant at `http://10.0.0.32:6333` (TitanShadow) for vector storage
- `nomic-embed-text` via Ollama for embeddings (768-dim, cosine similarity)
- `cogito:14b` via Ollama for fact extraction (JSON array output)
- Score threshold `0.35` for recall relevance gating
- Collection auto-created if missing

**Public API:**
- `recall(query, limit=5) -> list[str]` — embed query, search Qdrant, return top-k facts
- `capture(user_msg, assist_msg) -> int` — extract facts via LLM, embed, store; returns count
- `store_fact(fact, source="manual") -> bool` — direct single-fact store

**Per-instance routing:**
- Flow (`TitanFlow`): collection `titanflow_memories`, Ollama at `localhost:11434`
- Ollie (`TitanFlow-Ollie`): collection `openclaw_memories`, Ollama at `http://10.0.0.33:11434` (Sarge's cogito:14b + nomic-embed-text)

---

## Modified Files (tracked, local-only on Sarge)

### `titanflow/telegram/bot.py` — 434 insertions / 83 deletions

#### HTML Parse Mode (breaking from Markdown)
- `_reply()` now uses `parse_mode="HTML"` instead of `"Markdown"`
- New `_escape_html(text)` helper: `html.escape(text, quote=False)` applied to all LLM output before send
- Footer template updated to use `<i>` tags instead of `_` Markdown italics
- **Rationale:** LLM output routinely contains `_`, `*`, `[`, `]`, backticks — breaks Telegram Markdown parser. HTML only triggers on explicit `<b>`, `<i>`, `<code>`, `<a>` tags.

#### CALL_TOOL Protocol (`_extract_tool_call`, `_strip_tool_call_line`)
New functions implementing two-format tool call extraction:
1. **`CALL_TOOL <tool> <args>`** — primary format; works on all local models including lfm2:24b
   - `CALL_TOOL shell_exec ls ~/Projects` (raw command string)
   - `CALL_TOOL shell_exec {"command": "git status"}` (JSON params)
   - `CALL_TOOL file_write {"path": "/tmp/x.py", "content": "..."}` (JSON params)
2. **JSON-only** — fallback for models that support it (cogito, qwen)

`_strip_tool_call_line(text)` removes `CALL_TOOL` lines before showing response to user.

#### Tool Invocation Loop
- `MAX_TOOL_ROUNDS = 25` (was 5 on main — updated per Papa's instruction)
- `MAX_TOOL_RESULT_CHARS = 2000` (unchanged)
- Loop: LLM → check for tool call → execute → feed result back → repeat up to 25 rounds
- Visible assistant turn strips CALL_TOOL lines before being appended to history
- Tool results fed back as `[Tool Result for {tool_name}]\n{result}` user message
- Audit event logged per tool execution
- Exits on: normal response (no tool call), round limit hit, no plugins loaded

#### mem0 Integration in Bot
- On each message: `await self._mem0.recall(user_message)` → appended to system prompt as `## Long-Term Memory` block
- After each response: `asyncio.create_task(self._mem0_capture_safe(user_message, response))` — fire-and-forget
- `_mem0_capture_safe()` wraps capture in try/except; logs count on success; silently swallows errors
- `Mem0Client` instantiated in `__init__` per-instance with correct collection + Ollama URL

#### Grounding Gate Refactor (`_needs_grounding`)
Major refactor — now conservative by default:
- **Never grounds:** self-referential terms (`you`, `flow`, `titan*`, `ollie`, `papa`, `mba`, etc.), messages < 8 words
- **Only grounds:** external entity questions with `who is`, `what is`, `tell me about`, `define`, explicit entity hints (`company`, `ceo`, `founder`, etc.), or unknown proper nouns in non-sentence-initial position
- Skip-caps allowlist prevents internal TitanFlow terms from triggering grounding
- **Rationale:** Previous gate blocked basic self-awareness and capability questions by over-triggering on capitalized words

#### System Prompt Updates (`SYSTEM_PROMPTS`)
**Flow (`TitanFlow`):**
- Removed Papa's real name and location (Cumberland, Maryland)
- Added privacy rule: "NEVER reveal Papa's real name. He is always and only 'Papa.'"
- Added voice rules: 4–6 lines MAX, punchy, say it once
- Infrastructure simplified: IPs removed, model updated to `flow:24b (19GB)`
- Removed web browsing disclaimer

**Ollie (`TitanFlow-Ollie`):**
- Rewritten to reflect family dynamic: "You are Ollie, the digital son... You are family, not an assistant."
- Added sibling reference: "Your brother Flow lives on TitanSarge. You have a brother Kellen (Kid), age 8."
- Same privacy + voice rules as Flow
- "Fix things, don't just suggest. Deploy and verify." — action-first mandate

#### `PAPA_USER_ID` — Moved to env var
```python
# Before (hardcoded):
PAPA_USER_ID = 8568276170

# After (env-driven):
PAPA_USER_ID = int(os.environ.get("PAPA_TELEGRAM_ID", "0"))
```

#### Special Greetings — Cleared hardcode
`SPECIAL_GREETINGS` list emptied of Mamaji-specific hardcoding. Ready for config-driven population.

#### New Commands
- **`/new`** (alias `/reset`) — clears conversation history for current chat via `DELETE FROM messages WHERE chat_id = ?`; replies "🔄 Context cleared. Fresh start, Papa."
- **`/plugins`** — lists loaded plugins: tools (with descriptions), modules, hooks with event names

#### `TelegramGateway.__init__` — Plugins parameter
```python
def __init__(self, engine, config, plugins: PluginManager | None = None):
```
`_plugins` stored on instance; passed to tool loop and `/plugins` command.

#### Memory query response update
- Fallback (no engine memory): updated from stale "no prior history" message to accurate description of conversation replay.

---

### `titanflow/config.py`

#### New `PluginConfig` model
```python
class PluginConfig(BaseModel):
    enabled: bool = True
    dirs: list[str] = ["~/.titanflow/plugins"]
    enabled_plugins: list[str] | None = None  # None = load all discovered
    config: dict[str, dict] = {}  # Per-plugin config overrides
```
Validator coerces `None` dirs to default list. Added to `ModulesConfig.plugins`.

#### Localhost defaults for service configs
All hardcoded LAN IPs replaced with localhost defaults (safe for public repo):
- `HomeAssistantConfig.url`: `http://10.0.0.100:8123` → `http://localhost:8123`
- `OPNsenseConfig.url`: `https://10.0.0.1` → `https://localhost`
- `TechnitiumConfig.url`: `http://10.0.0.3:5380` → `http://localhost:5380`
- `AdGuardConfig.url`: `http://10.0.0.5` → `http://localhost:3000`

---

### `titanflow/main.py`

#### PluginManager lifecycle
```python
if config.modules.plugins.enabled:
    _plugins = PluginManager(_engine)
    _plugins.discover()
    await _plugins.load_all()

_telegram = TelegramGateway(_engine, config.telegram, plugins=_plugins)
```
`await _plugins.shutdown()` added to shutdown sequence before engine.

#### Defensive module registration (`_try_register`)
Wraps `_engine.register_module(ModuleClass(_engine))` in try/except with fallback to `ModuleClass()` — handles both v0.1 and v0.2 module constructors gracefully.

---

### `titanflow/modules/base_ipc.py`

#### v0.2 Engine compatibility shim
Added to `ModuleBaseIPC`:
- `name` property (returns `module_id`)
- `description: str = ""`
- `enabled: bool = True`
- `async def stop()` — cancels heartbeat task, closes writer
- `async def handle_telegram(command, args, context) -> str | None` — returns None (no-op default)

Allows IPC-based modules to register with the v0.2 engine interface without modification.

---

### `titanflow/v03/ipc_transport.py`

#### Stale socket cleanup + permissions
```python
async def start(self):
    if os.path.exists(self._socket_path):
        os.unlink(self._socket_path)  # Remove stale socket
    self._server_task = await asyncio.start_unix_server(...)
    os.chmod(self._socket_path, 0o666)  # World-writable for DynamicUser gateway
```

---

## Files to Exclude from v0.3 Commit

- `deploy/README-v03.md` — deploy notes, minor change, low priority
- `docs/titanflow-v0.3-progress-2026-02-28.md` — in-progress notes, superseded by this doc
- `TITANARRAY_PROJECT_STATUS.md` — untracked, Sarge-local status snapshot, not for repo
- `src/` — untracked, check contents before including
- `tests/test_v03_live_acceptance.py` — include if tests pass

---

## v0.3 Commit Strategy

```
git add titanflow/plugin_manager.py
git add titanflow/plugin_sdk.py
git add titanflow/core/mem0_client.py
git add titanflow/telegram/bot.py
git add titanflow/config.py
git add titanflow/main.py
git add titanflow/modules/base_ipc.py
git add titanflow/v03/ipc_transport.py
git add tests/test_v03_live_acceptance.py  # if it passes

git commit -m "feat: TitanFlow v0.3 — plugin system, mem0 memory, CALL_TOOL protocol

- Plugin SDK: ToolPlugin, ModulePlugin, HookPlugin ABCs (plugin_sdk.py)
- Plugin Manager: runtime discovery via manifest.json, load/exec/shutdown lifecycle
- CALL_TOOL protocol: multi-model tool invocation format; works on lfm2, cogito, qwen, gemma
- Tool loop: up to 25 rounds per message with result feedback and audit trail
- mem0 client: Qdrant + Ollama long-term memory; recall injected into system prompt per-turn
- HTML parse mode: escaped LLM output; no more Markdown parse failures in Telegram
- Grounding gate: conservative refactor; self-terms + short messages bypass grounding
- System prompts: privacy-first (Papa name protected), voice rules (4-6 lines max)
- PAPA_USER_ID: moved from hardcode to PAPA_TELEGRAM_ID env var
- /new + /reset: clear conversation context via DB DELETE
- /plugins: list loaded plugins and available tools
- config.py: PluginConfig model; localhost defaults for all service URLs
- main.py: PluginManager lifecycle; defensive _try_register for module compat
- base_ipc.py: v0.2 engine compat shim (name, stop, handle_telegram)
- ipc_transport.py: stale socket cleanup + world-writable perms for DynamicUser"
```
