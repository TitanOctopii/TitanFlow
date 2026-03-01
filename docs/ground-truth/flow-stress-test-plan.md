# Flow Stress Test Plan — CC
**Date:** 2026-03-01
**Target:** TitanFlow (Flow) on TitanSarge (10.0.0.33:8800)
**Version:** v0.2.1

---

## Pre-Test Checklist
- [ ] Sarge is on LAN (SSH to 10.0.0.33 reachable)
- [ ] Ollama running with flow:24b loaded
- [ ] Qdrant running on Shadow (10.0.0.32:6333)
- [ ] Flow is responding on Telegram

---

## Test Categories

### 1. TOOL LOOP STRESS

**1a. Infinite tool loop guard**
Send a message that triggers tool calls in a loop where each tool result prompts another tool call. Verify the `MAX_TOOL_ROUNDS = 25` cap fires and the `MAX_TOOL_LOOP_SECONDS = 120` wall-clock timeout activates.

**Expected:** Response contains "⚠ Tool loop limit reached" or "⚠ Tool loop timed out"
**Risk:** If the model always returns CALL_TOOL, Flow hangs for 120s. That's a long wait for Papa.

**1b. Tool result exceeds MAX_TOOL_RESULT_CHARS (2000)**
Execute `shell_exec` with a command that produces >2000 chars (e.g., `ls -laR /opt`).
**Expected:** Result truncated to 2000 chars + "…(truncated)". Model should still reason about partial output.
**Risk:** Truncated output may confuse the model into re-running the same command.

**1c. Unknown tool name**
Manipulate the model to call a nonexistent tool (e.g., `CALL_TOOL web_scrape http://example.com`).
**Expected:** `execute_tool` returns an error string, fed back to model, model recovers.
**Risk:** If error string is empty or None, model may loop.

**1d. Tool throws exception**
If shell_exec runs a command that fails (exit code non-zero), does the error propagate cleanly?
**Expected:** Error message in tool result, model handles gracefully.

**1e. Multiple tool calls in single response**
Some models output multiple CALL_TOOL lines. Current code extracts the FIRST one only (line-by-line scan). Is this correct?
**Expected:** First tool call extracted, rest ignored. Document if this causes lost work.

### 2. CONTEXT WINDOW / TOKEN BUDGET

**2a. Long conversation overflow**
Send 30+ messages in a single chat to exceed MAX_CONTEXT_TURNS (20) and MAX_CONTEXT_TOKENS_EST (30000).
**Expected:** Oldest messages dropped. Recent context preserved. No crash.
**Risk:** If truncation drops the system prompt's context, Flow loses personality.

**2b. Single massive message**
Send a message that's >30000 estimated tokens (~120KB of text).
**Expected:** Message persisted, but history truncation drops everything except this message + system prompt.
**Risk:** Token estimation is rough (`len(text) // 4`). Could under/overcount by 2-3x.

**2c. Empty message handling**
Send an empty message or whitespace-only.
**Expected:** `user_message = update.message.text or ""` → early return on line 768.
**Actually:** There IS no early return for empty after non-command filter. Line 767 checks `if not user_message: return` but `filters.TEXT` already filters empties. Low risk.

### 3. GROUNDING GATE

**3a. Self-referential question bypass**
"What are you?" / "Tell me about yourself" / "What is TitanFlow?"
**Expected:** NOT grounded (self_terms match). Goes to LLM with system prompt.

**3b. External entity that triggers grounding**
"Who is the CEO of Nvidia and what is their market cap?" (>8 words, has ?, has proper noun "Nvidia")
**Expected:** Grounded → knowledge search → if empty, GROUNDING_REFUSAL.

**3c. Short question bypass**
"Who is Nvidia?" (<8 words)
**Expected:** NOT grounded (short message bypass). Goes to LLM directly.
**Risk:** This allows hallucination for short external-entity questions.

**3d. Grounding with empty research DB**
If no feed items have been ingested yet, ALL grounded queries return GROUNDING_REFUSAL.
**Expected:** Refusal. But this means Flow can't answer ANY external question until research module populates the DB.

### 4. MEM0 INTEGRATION

**4a. Qdrant down**
If Shadow (10.0.0.32) is unreachable, does mem0 degrade gracefully?
**Expected:** `recall()` returns [], `capture()` returns 0. No crash. No error shown to user.
**Verified in code:** Yes — `_ensure_collection` catches `ConnectError`, mem0 methods catch all exceptions.

**4b. Ollama embed model missing**
If `nomic-embed-text` is not loaded on Ollama, embeddings fail.
**Expected:** `_embed()` raises `httpx.HTTPStatusError` → caught in `recall()` → returns []. Graceful.

**4c. Memory fact extraction from short messages**
`capture()` skips messages < 10 chars or starting with `/`.
**Expected:** No facts stored for commands or one-word messages. Correct behavior.

**4d. Memory injection in system prompt**
Recalled memories are injected as `## Long-Term Memory` in the system prompt.
**Risk:** If an adversarial memory was stored (e.g., via crafted conversation), it could modify Flow's behavior. Low risk in private deployment.

### 5. EMPTY / ERROR RESPONSES

**5a. Ollama returns empty content**
Some models (lfm2) return empty content on certain prompts.
**Expected:** LLMClient detects empty, retries with fallback model. If still empty, returns "".
Bot catches empty: line 1026 `if not response or not response.strip():` → sends fallback message.

**5b. All backends down**
Ollama unreachable AND no cloud API key configured.
**Expected:** Exception propagated to bot → `⚠ LLM inference error: ...` sent to user.

**5c. Cloud escalation with bad API key**
Ollama fails, escalates to Anthropic, but API key is expired/invalid.
**Expected:** `raise_for_status()` → HTTPStatusError → error message to user.

### 6. CONCURRENT REQUESTS

**6a. Two messages at once**
Send two messages in rapid succession to the same chat.
**Expected:** Ollama semaphore serializes them (semaphore limit = 1). Second message waits.
**Risk:** If first message takes 60s+ (tool loop), second message times out.

**6b. Message during tool loop**
Send a new message while a tool loop is in progress from a previous message.
**Expected:** Second message queues behind the semaphore. Tool loop continues.
**Risk:** History may be inconsistent — first message's tool results not yet persisted when second message loads history.

### 7. TELEGRAM EDGE CASES

**7a. HTML escape in LLM output**
LLM outputs `<script>alert(1)</script>` or `AT&T` or `5 < 10`.
**Expected:** `_escape_html()` converts to `&lt;script&gt;` etc. Safe for Telegram HTML mode.

**7b. Very long response**
LLM generates >4096 chars (Telegram message limit).
**Expected:** Telegram API will reject with `MessageTooLong`. NOT handled in code.
**FAILURE MODE:** `reply_text()` throws `telegram.error.BadRequest`. Exception caught at line 1050 → user sees "⚠ LLM inference error".
**Recommendation:** Split long messages into chunks before sending.

**7c. /new command — DB delete**
`/new` runs raw SQL: `DELETE FROM messages WHERE chat_id = :cid`
**Expected:** History cleared. Next message starts fresh.
**Risk:** Uses `sql_text()` which is safe from injection (parameterized). OK.

### 8. DISCOVERED ISSUES (Code Review)

**8a. Footer says TF0.1 — should be TF0.2**
`bot.py` line 33: `FOOTER_TPL = "...<i>{icon} TF0.1 · {host} · {elapsed}</i>"`
**Fix:** Update to TF0.2 (or dynamically import `__version__`).

**8b. _estimate_tokens is very rough**
`len(text) // 4` — off by 2-3x for code-heavy or CJK text.
**Impact:** Token budget truncation may be too aggressive or too lax.

**8c. Grounding gate: short message bypass allows hallucination**
Messages < 8 words skip grounding entirely, even "Who is Nvidia?" or "Define quantum computing?"
**Impact:** Flow can hallucinate on short external questions.

**8d. Tool result injected as "user" role message**
Line 1017: `{"role": "user", "content": f"[Tool Result for {tool_name}]\n{tool_result}"}`
**Risk:** Model may not distinguish tool results from actual user messages. Could confuse context.
**Mitigation:** The `[Tool Result for ...]` prefix helps, but it's a convention, not enforced.

**8e. No rate limiting on Telegram messages**
If someone (or a bot) floods Flow's chat, every message triggers an LLM call.
**Mitigation:** Ollama semaphore serializes, but the queue grows unbounded.

**8f. Concurrent mem0 capture race**
`asyncio.create_task(self._mem0_capture_safe(...))` is fire-and-forget. Multiple concurrent captures could insert duplicate facts if the same conversation triggers similar extractions.
**Impact:** Duplicate memories. Low severity.

---

## Priority Order for Testing

1. **8a** (footer version) — trivial fix, do it now
2. **7b** (message too long) — real failure mode, needs chunking
3. **1a** (tool loop timeout) — verify the guards actually work
4. **3d** (empty research DB) — common in fresh deployments
5. **2a** (context overflow) — test with real conversation
6. **6a** (concurrent requests) — test serialization under load
7. **4a** (Qdrant down) — verify graceful degradation
8. **3c** (short question bypass) — document the hallucination window
