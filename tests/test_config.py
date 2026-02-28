import textwrap
from pathlib import Path

from titanflow.config import load_config


def test_allowed_users_none_coerced(tmp_path, monkeypatch):
    cfg_path = tmp_path / "titanflow.yaml"
    cfg_path.write_text(
        textwrap.dedent(
            """
            titanflow:
              telegram:
                allowed_users: null
            """
        ).strip()
        + "\n"
    )
    monkeypatch.setenv("TITANFLOW_CONFIG", str(cfg_path))
    config = load_config(str(cfg_path))
    assert config.telegram.allowed_users == []


def test_nested_titanflow_key_parsing(tmp_path, monkeypatch):
    cfg_path = tmp_path / "titanflow.yaml"
    cfg_path.write_text(
        textwrap.dedent(
            """
            titanflow:
              name: "TitanFlow-Test"
              port: 9900
            """
        ).strip()
        + "\n"
    )
    monkeypatch.setenv("TITANFLOW_CONFIG", str(cfg_path))
    config = load_config(str(cfg_path))
    assert config.name == "TitanFlow-Test"
    assert config.port == 9900
