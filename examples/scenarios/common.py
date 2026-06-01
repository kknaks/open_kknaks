"""Shared helpers for example scenarios."""

import json
import os
from typing import Any

from open_kknaks.constants import DEFAULT_PROVIDER, PROVIDER_CODEX


def task_defaults() -> dict[str, Any]:
    """Build provider fields from environment variables for E2E demos."""
    provider = os.environ.get("OPEN_KKNAKS_PROVIDER", DEFAULT_PROVIDER)
    model = os.environ.get("OPEN_KKNAKS_MODEL") or None

    options: dict[str, Any] = {}
    if cwd := os.environ.get("OPEN_KKNAKS_CWD"):
        options["cwd"] = cwd
    if timeout := os.environ.get("OPEN_KKNAKS_TIMEOUT_SEC"):
        options["timeout_sec"] = int(timeout)

    provider_options: dict[str, Any] = {}
    if raw := os.environ.get("OPEN_KKNAKS_PROVIDER_OPTIONS"):
        provider_options.update(json.loads(raw))
    if provider == PROVIDER_CODEX:
        provider_options.setdefault("sandbox", os.environ.get("OPEN_KKNAKS_CODEX_SANDBOX", "workspace-write"))
        provider_options.setdefault("color", "never")
        provider_options.setdefault("skip_git_repo_check", True)

    return {
        "provider": provider,
        "model": model,
        "options": options,
        "provider_options": provider_options,
    }


def describe_task_defaults(defaults: dict[str, Any]) -> str:
    model = defaults["model"] or "(default)"
    return (
        f"provider={defaults['provider']} model={model} "
        f"options={defaults['options']} provider_options={defaults['provider_options']}"
    )
