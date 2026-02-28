# TitanFlow FIXES — Phase 1.1

Deployment bugs fixed during initial deploy + architectural improvements for next iteration.

---

## Bugs Fixed During Deploy (v0.1.0 → v0.1.1)

### 1. Config: `allowed_users` crashes on empty YAML list
- **File:** `titanflow/config.py`
- **Issue:** YAML parses a commented-out list as `None`, Pydantic expects `list[int]`
- **Fix:** Added `field_validator` to coerce `None` → `[]`

### 2. Scheduler: `IntervalTrigger` rejects `None` kwargs
- **File:** `titanflow/core/scheduler.py`
- **Issue:** `add_interval(seconds=7200, minutes=None, hours=None)` passes `None` to `timedelta()` which raises `TypeError`
- **Fix:** Filter out `None` values before constructing `IntervalTrigger`

### 3. Ollama SDK: `.list()` returns Pydantic objects, not dicts
- **File:** `titanflow/core/llm.py`
- **Issue:** Code used `m["name"]` but Ollama SDK >=0.4 returns objects with `.model` attribute
- **Fix:** Use `getattr(m, "model", ...)` with dict fallback

### 4. Database: SQLAlchemy `AsyncSession` lacks `.exec()`
- **File:** `titanflow/core/database.py`
- **Issue:** `sessionmaker` created SQLAlchemy's `AsyncSession` but modules call `.exec()` which is SQLModel-specific
- **Fix:** Import `AsyncSession` from `sqlmodel.ext.asyncio.session` instead of `sqlalchemy.ext.asyncio`

### 5. httpx: Redirects not followed
- **File:** `titanflow/modules/research/module.py`
- **Issue:** httpx defaults to `follow_redirects=False`, causing 301/302 feeds (arXiv, Google, GitHub) to fail
- **Fix:** Set `follow_redirects=True` on the `AsyncClient`

### 6. Stale feed URLs in config
- **Files:** `config/feeds.yaml`, `config/github_repos.yaml`
- **Issue:** Several URLs have moved: OpenAI blog, Google AI blog, llama.cpp repo (ggerganov → ggml-org)
- **Fix:** Updated URLs to current locations
- **Still 404:** Anthropic (`/rss.xml`), Ollama blog, Meta AI blog — need manual lookup

### 7. Systemd unit env var typo
- **File:** `titanflow.service`
- **Issue:** `TITANCLAW_CONFIG` should be `TITANFLOW_CONFIG`
- **Status:** Not yet fixed on disk — noted for when service is installed

---

## Phase 1.1 Architectural Improvements

### A. SQLite WAL Mode
- **Why:** Multiple modules write concurrently (research fetching, security logging, newspaper publishing). Default journal mode can cause "database is locked" under async load.
- **Fix:** After engine creation, execute `PRAGMA journal_mode=WAL` on the connection.
- **Where:** `titanflow/core/database.py` in `init()`

### B. LLM Request Priority (Telegram > Batch)
- **Why:** A 14B model generating a newspaper article can take minutes. If a Telegram user sends a message during generation, it queues behind it.
- **Fix:** Add an `asyncio.Semaphore` or priority queue to `LLMClient`. Telegram requests get immediate access; batch jobs (research processing, article generation) yield.
- **Where:** `titanflow/core/llm.py`

### C. Structured Logging via structlog
- **Why:** Current `logging.basicConfig` produces unstructured text. Hard to grep, hard to parse, no module/event context in a structured format.
- **Fix:** Replace stdlib logging with `structlog`. Add context (module name, event type, duration) to every log line. Output as JSON in production, colored text in dev.
- **Where:** `titanflow/main.py` + all modules

### D. Retry Policies on External APIs
- **Why:** Ghost CMS, GitHub API, RSS feeds — all can be temporarily down. Currently a single failure = skipped item with no retry.
- **Fix:** Add exponential backoff retry (3 attempts, 2/4/8s delays) on `httpx` calls. Use `tenacity` or a simple decorator.
- **Where:** `titanflow/modules/research/module.py`, `titanflow/modules/newspaper/module.py`

### E. Draft Queue Before Auto-Publish
- **Why:** LLM-generated articles published without review to a public site risk hallucinated facts, broken formatting, or embarrassing content.
- **Fix:** Default to `status=draft` for first N articles. Add `/review` Telegram command to approve drafts. Add a confidence gate based on article length and structure validation before auto-publish.
- **Where:** `titanflow/modules/newspaper/module.py`

### F. Feed URL Health Check on Startup
- **Why:** 3 feeds still 404. Stale URLs silently fail every 2 hours forever.
- **Fix:** On startup, test each feed URL with a HEAD request. Log warnings for unreachable feeds. Add `/feeds` Telegram command showing feed health.
- **Where:** `titanflow/modules/research/module.py`
