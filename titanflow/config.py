"""TitanFlow configuration — single source of truth."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings


DEFAULT_CONFIG_PATH = "/opt/titanflow/config/titanflow.yaml"

class LLMCloudConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-5-20250929"
    api_key: str = ""
    escalation_threshold: float = 0.7


class LLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    default_model: str = "flow:24b"
    fallback_model: str = "qwen3-coder-next"
    cloud: LLMCloudConfig = LLMCloudConfig()


class TelegramConfig(BaseModel):
    bot_token: str = ""
    allowed_users: list[int] = Field(default_factory=list)

    @field_validator("allowed_users", mode="before")
    @classmethod
    def _coerce_none_to_list(cls, v):
        return v if v is not None else []


class HomeAssistantConfig(BaseModel):
    url: str = "http://localhost:8123"
    token: str = ""
    enabled: bool = True


class OPNsenseConfig(BaseModel):
    url: str = "https://localhost"
    key: str = ""
    secret: str = ""
    enabled: bool = True


class GhostSiteConfig(BaseModel):
    url: str = ""
    admin_key: str = ""
    enabled: bool = True


class GhostConfig(BaseModel):
    titanarray: GhostSiteConfig = GhostSiteConfig()
    titanflow: GhostSiteConfig = GhostSiteConfig()


class GitHubConfig(BaseModel):
    token: str = ""
    enabled: bool = True


class TechnitiumConfig(BaseModel):
    url: str = "http://localhost:5380"
    token: str = ""
    enabled: bool = True


class AdGuardConfig(BaseModel):
    url: str = "http://localhost:3000"
    username: str = ""
    password: str = ""
    enabled: bool = True


class IntegrationsConfig(BaseModel):
    home_assistant: HomeAssistantConfig = HomeAssistantConfig()
    opnsense: OPNsenseConfig = OPNsenseConfig()
    ghost: GhostConfig = GhostConfig()
    github: GitHubConfig = GitHubConfig()
    technitium: TechnitiumConfig = TechnitiumConfig()
    adguard: AdGuardConfig = AdGuardConfig()


class DatabaseConfig(BaseModel):
    path: str = "/data/titanflow/titanflow.db"


class ModuleToggle(BaseModel):
    enabled: bool = True


class ResearchModuleConfig(ModuleToggle):
    fetch_interval: int = 7200  # seconds
    max_items_per_feed: int = 50
    processing_batch_size: int = 50  # items per LLM processing cycle


class NewspaperModuleConfig(ModuleToggle):
    site: str = "titanflow.space"
    auto_publish: bool = True
    morning_briefing: str = "06:00"
    evening_digest: str = "18:00"
    weekly_review: str = "sunday 08:00"


class SecurityModuleConfig(ModuleToggle):
    alert_cooldown: int = 3600
    poll_interval: int = 300


class CodeExecConfig(ModuleToggle):
    enabled: bool = False
    timeout: int = 30
    max_output: int = 4096


class PluginConfig(BaseModel):
    enabled: bool = True
    dirs: list[str] = Field(default_factory=lambda: ["~/.titanflow/plugins"])
    enabled_plugins: list[str] | None = None  # None = load all discovered
    config: dict[str, dict] = Field(default_factory=dict)  # Per-plugin config overrides

    @field_validator("dirs", mode="before")
    @classmethod
    def _coerce_none_to_list(cls, v):
        return v if v is not None else ["~/.titanflow/plugins"]


class ModulesConfig(BaseModel):
    security: SecurityModuleConfig = SecurityModuleConfig()
    home: ModuleToggle = ModuleToggle()
    automation: ModuleToggle = ModuleToggle()
    webpub: ModuleToggle = ModuleToggle()
    research: ResearchModuleConfig = ResearchModuleConfig()
    newspaper: NewspaperModuleConfig = NewspaperModuleConfig()
    codeexec: CodeExecConfig = CodeExecConfig()
    plugins: PluginConfig = PluginConfig()


class TitanFlowConfig(BaseModel):
    """Root configuration object."""

    name: str = "TitanFlow"
    host: str = "0.0.0.0"
    port: int = 8800
    debug: bool = False
    api_key: str = ""
    config_dir: str = "/opt/titanflow/config"
    llm: LLMConfig = LLMConfig()
    telegram: TelegramConfig = TelegramConfig()
    integrations: IntegrationsConfig = IntegrationsConfig()
    database: DatabaseConfig = DatabaseConfig()
    modules: ModulesConfig = ModulesConfig()


def _resolve_env_vars(data: Any) -> Any:
    """Recursively resolve ${ENV_VAR} references in config values."""
    if isinstance(data, str) and data.startswith("${") and data.endswith("}"):
        env_key = data[2:-1]
        return os.environ.get(env_key, "")
    elif isinstance(data, dict):
        return {k: _resolve_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_resolve_env_vars(v) for v in data]
    return data


def load_config(config_path: str | Path | None = None) -> TitanFlowConfig:
    """Load configuration from YAML file with environment variable resolution."""
    if config_path is None:
        config_path = os.environ.get(
            "TITANFLOW_CONFIG", DEFAULT_CONFIG_PATH
        )

    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        # Support nested under 'titanflow:' key or flat
        if "titanflow" in raw and isinstance(raw["titanflow"], dict):
            data = raw["titanflow"]
        else:
            data = raw
        resolved = _resolve_env_vars(data)
        resolved.setdefault("config_dir", str(path.parent))
        return TitanFlowConfig(**resolved)

    # No config file — use defaults + env vars
    return TitanFlowConfig()
