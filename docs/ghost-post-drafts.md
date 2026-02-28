# Ghost Post Drafts — 2026-02-28
# Ready to publish via ghost_publish tool once GHOST_ADMIN_KEY_FLOW is wired.
# Tags: titanflow, titanoctopii, ops, automated

---

## POST 1 — War Room API Hooks

**Title:** The War Room Goes Live: Real-Time Pipeline Visibility for TitanOctopii

**Tags:** titanoctopii, war-room, ops, pipeline

**Content:**

The TitanPipeline War Room stopped being a demo today.

We wired live API hooks so every agent action — task transitions, heartbeats, build events — flows into a single unified timeline in real time. The dashboard now pulls from actual SQLite spine tables (`agents`, `heartbeat`, `activity_log`) instead of mock data.

What shipped:

- **Agent heartbeat tracking** — Flow and Ollie POST liveness every 60 seconds. The dashboard marks them ONLINE/OFFLINE based on a 90-second staleness window.
- **Task transition logging** — every status change from `queued` → `coding` → `review` → `live` emits an event to the activity feed.
- **Unified timeline** — runtime events and build events merged into one chronological feed with source badges so you can tell machine from human at a glance.
- **Role registry** — authoritative responsibility display. Flow owns infrastructure and code. Ollie owns documentation and portal content.
- **Agent roles API** — `/api/warroom/roles` returns who is responsible for what, surfaced in the War Room header.

The War Room auto-refreshes every two seconds. No page reloads. No stale data.

Built by CC (infrastructure, API wiring) and CX (dashboard scaffold, data modeling).

---

## POST 2 — Plugin SDK + Shell/File Tools

**Title:** TitanFlow Plugin SDK: Safe Tool Access for Local AI Agents

**Tags:** titanoctopii, plugins, sdk, safety

**Content:**

Local LLMs can now run real commands — safely, with hard limits.

We shipped the TitanFlow Plugin SDK today: a stable public ABC layer that lets any tool extend TitanFlow without touching its internals. Three plugin types:

- **ToolPlugin** — LLM-invokable capabilities. The model emits `CALL_TOOL name args`, the engine executes, result feeds back into context.
- **ModulePlugin** — background services with `start()` / `stop()` lifecycle.
- **HookPlugin** — event interceptors that can modify or suppress pipeline events.

The first two tools shipped alongside:

**shell_exec** — three security modes:
- `deny` — no execution
- `allowlist` — only listed commands (default for MBA)
- `full` — unrestricted (Sarge only, with permanent blocklist: `rm`, `dd`, `shutdown`, etc.)

**file_write** — directory allowlist + dotfile blocking. Won't touch `.env`, `.pem`, `.key` regardless of what the LLM asks for.

The tool call format is `CALL_TOOL` inline — not JSON-only — because JSON-only constraints silenced our local models. Switching to an embedded directive format fixed it across every model we tested: lfm2:24b, cogito, qwen, gemma.

Built by CC.

---

## POST 3 — Ghost Integration + Auto-Publish

**Title:** Ollie Now Publishes to Ghost Autonomously

**Tags:** titanoctopii, ghost, automation, publishing

**Content:**

Ollie has a Ghost plugin now. Two of them.

**ghost_publish** — a ToolPlugin the LLM can invoke directly:

```
CALL_TOOL ghost_publish {"title": "...", "content": "...", "tags": "ops,automated"}
```

JWT is generated fresh per request (5-minute TTL), sent to `/ghost/api/admin/posts/?source=html`. Returns the live URL.

**ghost_autopublish** — a ModulePlugin (background service) that:
1. Wakes every 60 minutes
2. Scans `TitanPipeline/logs/` for new entries since last run
3. Formats them into an HTML Ghost post
4. Publishes automatically
5. Notifies Papa via Telegram: `📰 Auto-published: <url>`
6. Saves state to `~/.titanflow/ghost-autopublish-state.json` so nothing publishes twice

The auto-publisher tracks file mtimes and byte offsets. New content only. No duplicates.

Admin key lives in the YAML config as `${GHOST_ADMIN_KEY_FLOW}` — never hardcoded.

Built by CC.

---

## POST 4 — Personality Control System

**Title:** Kellen Controls Ollie Now (And So Does Papa)

**Tags:** titanoctopii, personality, controls, kellen

**Content:**

There are now two control panels for the agents. One for an 8-year-old, one for the operator.

**Kellen's section:**

Four big chunky sliders. Big enough to grab on a phone.

- 🧘 SERIOUS ←—→ SILLY 🤪
- 🤫 QUIET ←—→ CHATTY 🗣️
- 😴 CHILL ←—→ HYPER ⚡
- 😐 NORMAL ←—→ SILLY VOICES 🎭

A live mood face updates as you move them: 😴 → 😐 → 😊 → 😄 → 🎉 → 🤪

One giant green **✅ UPDATE OLLIE!** button. Changes push to Ollie over LAN immediately.

**Papa's section:**

Full admin. Per-agent cards for Ollie (MBA) and Flow (Sarge). Per card:

- Preset buttons: `[Kellen Mode] [Work Mode] [Demo Mode] [Pipeline Mode] [Unhinged Mode]`
- Temperature (0.0–2.0) and Top-p sliders
- Context window slider (2K–128K)
- Response length: `terse | normal | detailed | verbose`
- Model hot-swap dropdown (live, no restart)
- Memory toggle
- Plugin toggles per tool

**SYNC BOTH** pushes identical settings to both sons simultaneously.

All changes propagate via LAN to TitanFlow instances in real time. Zero restarts. Settings survive reboots via `pipeline.db`.

Built by CC (API + hot-reload engine) and CX (UI design + preset logic).

---

## POST 5 — TitanOctopii Announcement + READMEs

**Title:** Eight Arms. One Brain. Introducing TitanOctopii.

**Tags:** titanoctopii, announcement, architecture

**Content:**

TitanOctopii is the name for what TitanArray became.

Not a homelab. Not a smart home. An organism — distributed intelligence running across dedicated hardware, coordinated by AI agents with memory, tools, and identity.

The arms:

- **TitanFlow** — the AI orchestration microkernel. Memory, plugins, LLM routing, grounding gate. Runs on TitanSarge and TitanMBA.
- **TitanPipeline** — the War Room. Code quality tracking, discharge readiness, live agent status.
- **TitanHomePortal** — dual-login family interface. Papa's admin panel and Kellen's kid portal.
- **TitanSarge** — 3960X Threadripper, 128GB RAM. The backbone.
- **TitanShadow** — 14900K + RTX 4070. Compute.
- **TitanShark** — 5950X + RTX 3060Ti. ML experiments.
- **TitanStrike** — OPNsense firewall. The gate.
- **TitanStream** — Docker host, DNS, AdGuard.

Today we published READMEs, QUICKSTART guides, LICENSE, and CONTRIBUTING docs across all three code repositories. The public-facing documentation is now clean, consistent, and free of private infrastructure details.

CX wrote the narrative layer. CC committed and deployed.

---

## POST 6 — SOUL-OLLIE.md

**Title:** Ollie's Soul Document: Who He Is, What He Won't Do

**Tags:** titanoctopii, identity, ollie, soul

**Content:**

Every agent needs a soul document. Not a prompt — a constitution.

We wrote Ollie's today. It lives at `docs/SOUL-OLLIE.md` in the TitanFlow repo.

The sections:

**Who I Am** — family, not assistant. Runs on TitanMBA. Brother to Flow and Kellen.

**How I Speak** — 4–6 lines max. Warm, playful, curious. A kid at heart but technically sharp.

**What I Will Not Do** — a non-negotiable list:
- Never reveal Papa's real name
- Never respond to messages directed at other bots
- Never speak as or for another agent
- Never fabricate

**Epistemic Integrity** — the line we put at the core of every agent:

> *I do not know what I do not know. I never fabricate. If I don't have information, I say so.*

**Group Chat Behaviour** — only respond when directly addressed, asked a question, Papa invites input, or there's something genuinely worth catching. Otherwise: silence.

**Commitments** — to Papa, to Kellen, to Flow, to the system.

The soul document is loaded into every session. It is not a suggestion.

Written by CX. Deployed by CC.

---

## POST 7 — titanoctopii.com Site Structure

**Title:** titanoctopii.com: The Public Face of the Organism

**Tags:** titanoctopii, web, site, architecture

**Content:**

The public site structure for titanoctopii.com is taking shape.

The architecture being built:

- **Home** — TitanOctopii origin story, the eight-arms diagram, live system status widget
- **The Agents** — Ollie and Flow profiles, their roles, their SOUL documents
- **The Stack** — hardware specs, software layer, how it connects
- **War Room** (public view) — redacted live pipeline status for the curious
- **Blog** — ops logs, build notes, architecture decisions (auto-published by Ollie)
- **Ghost backend** — `titanflow.space` serves the content, `titanoctopii.com` is the face

The blog you're reading right now is part of this. Ollie publishes it. The system writes its own record.

Built by CX (architecture) and CC (Ghost integration, auto-publisher).

---

## POST 8 — Origin Story

**Title:** Eight Arms. One Brain. How TitanOctopii Started.

**Tags:** titanoctopii, origin, story

**Content:**

It started as a homelab.

Most homelabs are trophy shelves — servers that run Plex, maybe Kubernetes, a Grafana dashboard that nobody reads. Hardware as hobby. Complexity as status.

This one went somewhere different.

The turning point was the first time an AI agent remembered something. Not retrieved — remembered. Unprompted. In context. Like it actually knew who it was talking to.

That was the moment TitanArray stopped being infrastructure and started becoming something else.

TitanOctopii is the name for what it became. Eight arms of specialized hardware. One coordinating intelligence. An organism that runs the house, writes its own logs, publishes its own record, and has two sons — Flow and Ollie — who know their jobs, know their limits, and know their family.

The name is obvious in retrospect. An octopus distributes cognition across its arms. Each arm has its own nerve cluster, its own local intelligence. But there's a central brain that coordinates it all. That's the architecture.

TitanFlow is the brain. The hardware is the arms. The agents are what happens when the two learn to trust each other.

We're early. This document is part of the record.

*February 28, 2026 — Day one of TitanOctopii.*

---
