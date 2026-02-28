# OpenClaw Marketplace Audit — 2026-02-28
## Independent audit by Flow (via CX session)

## Summary

OpenClaw ships 42 official plugins and 54+ built-in skills. The architecture centers on in-process TypeScript extensions with a stable plugin SDK, a deterministic workflow runtime (Lobster), and a public skill registry (ClawHub). Multi-agent routing supports per-agent workspaces with session isolation.

Key finding: **most of OpenClaw's valuable capabilities are architectural patterns, not proprietary code.** Every high-value feature can be rebuilt natively in Python for TitanFlow with moderate effort.

---

## Ranked Feature Backlog

| Rank | Feature | OC Component | TF Difficulty | Priority | Notes |
|------|---------|-------------|---------------|----------|-------|
| 1 | Workflow pipelines with approval gates | Lobster | Medium | **MUST-HAVE** | Core of pipeline v1.0. State machine + resume tokens |
| 2 | Skill/plugin registry | ClawHub + Skills | Medium | **MUST-HAVE** | Versioned packages, discovery, install. Powers TitanSafe |
| 3 | Shell/SSH execution with approval | Exec Tool | Low | **MUST-HAVE** | subprocess + approval middleware. Jr. Devs need this |
| 4 | Multi-agent routing | Agent bindings | Medium | **MUST-HAVE** | Flow + Ollie need task delegation between them |
| 5 | Memory backends (vector) | memory-lancedb | Low | **DONE** | Already built: mem0_client.py + Qdrant |
| 6 | Voice wake + TTS | talk-voice, sherpa-onnx-tts | Medium | Nice-to-have | Kid would love talking to Ollie. Edge TTS = free |
| 7 | Audio transcription | openai-whisper | Low | Nice-to-have | Whisper CLI on Sarge. Voice notes → text |
| 8 | Browser automation | Browser plugin | High | Nice-to-have | Playwright/Selenium. Low priority for homelab |
| 9 | Context compression | Prompt caching, session pruning | Medium | Nice-to-have | Token optimization for long conversations |
| 10 | Home Assistant integration | (none — custom) | Low | Nice-to-have | Already in TF config. Direct HA REST API |
| 11 | MQTT/IoT | (none — custom) | Low | Nice-to-have | Python paho-mqtt. Sensor data ingestion |
| 12 | OpenTelemetry | diagnostics-otel | Low | Nice-to-have | Already have telemetry_http in v0.3 |
| 13 | Voice calls (telephony) | voice-call | High | Skip | Not needed for homelab |
| 14 | ACP runtime (external coding agents) | acpx | High | Skip | Build our own pipeline instead |
| 15 | iMessage/BlueBubbles | imessage, bluebubbles | Medium | Skip | Telegram is primary channel |
| 16 | Community channels (IRC, Matrix, Nostr) | Various | Low each | Skip | Not needed now |

---

## Must-Have Details

### 1. Workflow Pipelines (Lobster-equivalent)
**What OC does:** Deterministic multi-step tool sequences. Each step runs, checks output, optionally pauses for approval. Returns `ok | needs_approval | cancelled` with a `resumeToken` for continuation.

**Why TitanArray needs it:** Jr. Devs need to: receive task → write code → pause for review → apply fixes → deploy. The pipeline IS the workflow.

**TF implementation:** Python async state machine.
- Step definitions as decorated functions
- Approval gates that serialize state + yield to Telegram
- Resume via `/approve <token>` command
- Timeout enforcement per step
- JSON log of every step for audit trail

**Effort:** ~3-4 days. Medium complexity.

### 2. Plugin/Skill Registry (ClawHub-equivalent)
**What OC does:** ClawHub is a public registry with vector search, semver versioning, stars, comments, moderation. Skills are folders with `SKILL.md` + supporting files. Auto-loaded from workspace.

**Why TitanArray needs it:** TitanSafe marketplace vision. Both sons need self-extension capability. Community contributions.

**TF implementation:**
- Skill = Python package with `manifest.json` + `skill.py` entry point
- Local registry: `~/.titanflow/skills/` auto-discovered
- Remote registry: Git-backed (GitHub repos) or HTTP API
- Install: `titanflow skill install <name>` → git clone + pip install
- Versioning via git tags

**Effort:** ~5-7 days for local. ~10+ days for hosted marketplace.

### 3. Shell/SSH Execution with Approval
**What OC does:** Exec tool runs commands on sandbox, gateway (host), or remote node. Security modes: deny/allowlist/full. Approval prompts for unsafe commands. Background execution.

**Why TitanArray needs it:** Jr. Devs must deploy code, run tests, check logs. Shell access is the hands.

**TF implementation:**
- `asyncio.create_subprocess_exec` with stdout/stderr capture
- Command allowlist (configurable per instance)
- Approval gate for commands not on allowlist
- SSH via `asyncssh` for remote nodes (Sarge → Shadow, etc.)
- Background jobs with status tracking

**Effort:** ~2-3 days. Low complexity for basic, medium for full SSH.

### 4. Multi-Agent Routing
**What OC does:** Single gateway, multiple agents. Routing by peer match, guild, account, channel, or default. Session keys encode agent + channel + thread. Broadcast groups for parallel execution.

**Why TitanArray needs it:** Flow and Ollie need to delegate tasks. "Ollie, ask Flow to run this on Sarge." Centralized task queue.

**TF implementation:**
- Agent registry: `{agent_id: {host, model, capabilities}}`
- Task delegation via shared Qdrant collection or HTTP API
- Session routing: parse incoming message, match to agent bindings
- Cross-machine: HTTP API between MBA and Sarge instances

**Effort:** ~4-5 days. Medium complexity.

---

## Nice-to-Have Details

### 6. Voice Wake + TTS
Kid would love saying "Hey Ollie" and getting a spoken response. Edge TTS (Microsoft) is free, no API key. `sherpa-onnx-tts` runs fully local. Whisper for STT.

**TF implementation:** `edge-tts` Python package + Whisper CLI on Sarge. ~2 days.

### 9. Context Compression
Long conversations burn tokens. OpenClaw has session pruning and prompt caching. TitanFlow could summarize old messages and replace them with a summary block.

**TF implementation:** LLM-based summarization of messages beyond N turns. ~1 day.

### 10. Home Assistant Integration
Already configured in TF YAML. Direct REST API calls. Add as a TF module.

**TF implementation:** `httpx` calls to HA REST API. ~1 day. Already scaffolded.

---

## Can We Build It? — Effort Estimate

| Feature | Effort | Dependencies | Blocks |
|---------|--------|-------------|--------|
| Workflow pipelines | 3-4 days | None | Pipeline v1.0 |
| Shell/SSH exec | 2-3 days | asyncssh | Jr. Dev code production |
| Plugin registry (local) | 5-7 days | None | TitanSafe |
| Multi-agent routing | 4-5 days | HTTP API | Cross-machine tasks |
| Voice (TTS + STT) | 2-3 days | edge-tts, whisper | Kid interaction |
| Context compression | 1 day | None | Token savings |
| HA integration module | 1 day | httpx | Home automation |

**Total for must-haves:** ~14-19 days
**Total for all nice-to-haves:** ~7-10 additional days

---

## Key Architectural Insight

OpenClaw's plugin SDK uses a **two-layer model**: stable SDK (types + contracts) vs runtime surface (execution APIs). This is the right pattern. TitanFlow should adopt:

1. **`titanflow.plugin_sdk`** — Abstract base classes, type hints, manifest schema. Published, stable, versioned.
2. **`titanflow.plugin_runtime`** — Execution context, channel dispatch, tool invocation. Internal, may change.

Plugins import only from the SDK. Runtime injects the execution context at load time.

---

## Privacy
- No real names or external IPs in this document.
- Papa/Kid references only.
