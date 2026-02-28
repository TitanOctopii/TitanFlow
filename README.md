# TitanFlow

Orchestration engine for TitanArray. Research, publishing, security, home integration, and automation — all from one system.

## Quick Start on TitanSarge

```bash
# Clone / copy to Sarge
cd /opt
git clone <repo> titanflow  # or scp the directory

# Create virtual environment
cd /opt/titanflow
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# Also need PyJWT for Ghost publishing
pip install pyjwt

# Copy and edit configuration
cp config/titanflow.yaml /opt/titanflow/config/titanflow.yaml
# Edit: add your Telegram bot token, Ghost keys, GitHub token, etc.

# Create data directory
sudo mkdir -p /data/titanflow
sudo chown kamal:kamal /data/titanflow

# Set config path
export TITANFLOW_CONFIG=/opt/titanflow/config/titanflow.yaml

# Run
python -m titanflow.main
```

## Systemd Service

```bash
sudo cp titanflow.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable titanflow
sudo systemctl start titanflow

# Check logs
journalctl -u titanflow -f
```

## API Endpoints

Once running, available at `http://localhost:8800`:

- `GET /` — Service info
- `GET /api/health` — Health check
- `GET /api/status` — Engine status with module info
- `GET /api/modules` — List all modules
- `GET /api/llm/health` — Ollama connection status
- `GET /api/jobs` — Scheduled jobs

## Telegram Commands

- `/status` — Engine overview
- `/modules` — Active modules
- `/jobs` — Scheduled tasks
- `/research` — Research module status
- `/latest` — Latest high-relevance items
- `/newspaper` — Publishing status
- `/publish briefing|digest|weekly` — Force a publish cycle

## Architecture

```
TitanFlow Engine
├── Core (FastAPI + EventBus + Scheduler + LLM + Database)
├── Telegram Gateway (command routing + natural language)
├── Research Module (RSS feeds, GitHub tracking, LLM analysis)
├── Newspaper Module (autonomous Ghost publishing to titanflow.space)
├── Security Module (Phase 2)
├── Home Module (Phase 2)
├── Automation Module (Phase 2)
└── WebPub Module (Phase 2)
```

## Configuration

All secrets use `${ENV_VAR}` syntax in YAML — set them as environment variables or in the systemd service file.
