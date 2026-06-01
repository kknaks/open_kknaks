"""Regression tests for example/demo provider contract."""

import importlib.util
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "examples" / "scenarios"


def _load_common() -> Any:
    spec = importlib.util.spec_from_file_location("example_common", SCENARIOS / "common.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scenario_defaults_use_claude_by_default(monkeypatch) -> None:
    common = _load_common()
    for key in (
        "OPEN_KKNAKS_PROVIDER",
        "OPEN_KKNAKS_MODEL",
        "OPEN_KKNAKS_CWD",
        "OPEN_KKNAKS_TIMEOUT_SEC",
        "OPEN_KKNAKS_PROVIDER_OPTIONS",
    ):
        monkeypatch.delenv(key, raising=False)

    assert common.task_defaults() == {
        "provider": "claude",
        "model": None,
        "options": {},
        "provider_options": {},
    }


def test_scenario_defaults_map_codex_environment(monkeypatch) -> None:
    common = _load_common()
    monkeypatch.setenv("OPEN_KKNAKS_PROVIDER", "codex")
    monkeypatch.setenv("OPEN_KKNAKS_MODEL", "gpt-5")
    monkeypatch.setenv("OPEN_KKNAKS_CWD", "/repo")
    monkeypatch.setenv("OPEN_KKNAKS_TIMEOUT_SEC", "123")
    monkeypatch.setenv("OPEN_KKNAKS_PROVIDER_OPTIONS", '{"output_last_message":"/tmp/out.txt"}')

    assert common.task_defaults() == {
        "provider": "codex",
        "model": "gpt-5",
        "options": {"cwd": "/repo", "timeout_sec": 123},
        "provider_options": {
            "output_last_message": "/tmp/out.txt",
            "sandbox": "workspace-write",
            "color": "never",
            "skip_git_repo_check": True,
        },
    }


def test_examples_do_not_use_removed_claude_client_or_session_id_kwarg() -> None:
    for path in (ROOT / "examples").rglob("*.py"):
        source = path.read_text()
        assert "ClaudeClient" not in source
        assert "session_id=session_id" not in source


def test_demo_template_exposes_provider_controls() -> None:
    source = (ROOT / "examples" / "app" / "templates" / "index.html").read_text()
    assert '<select id="provider">' in source
    assert '<option value="codex">codex</option>' in source
    assert "provider_options: providerOptions" in source
    assert "sandbox: 'workspace-write'" in source
    assert "skip_git_repo_check: true" in source
