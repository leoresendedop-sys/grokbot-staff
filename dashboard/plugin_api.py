"""Read-only backend for the Desktop pane: GET /api/plugins/grokbot-staff/overview."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()
_PLUGIN = "grokbot-staff"
_ROOT = Path(__file__).resolve().parent.parent


def _specialists_module():
    spec = importlib.util.spec_from_file_location("grokbot_staff_specialists", _ROOT / "specialists.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # local, reviewed source file shipped in this package
    return mod


def _settings() -> dict:
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
        return (((cfg.get("plugins") or {}).get("entries") or {}).get(_PLUGIN) or {}).get("settings") or {}
    except Exception:
        return {}


def _jobs() -> list:
    try:
        from plugins.plugin_storage import plugin_data_dir
        data = json.loads((plugin_data_dir(_PLUGIN) / "jobs.json").read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _webhook_set() -> bool:
    try:
        from agent.secret_scope import get_secret
        return bool((get_secret("GROKBOT_STAFF_WEBHOOK_URL") or "").strip())
    except Exception:
        return False


def _loop(settings: dict) -> dict:
    enabled = settings.get("quality_loop", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("0", "false", "no", "off", "")
    try:
        attempts = max(1, min(int(settings.get("max_attempts") or 3), 10))
    except (TypeError, ValueError):
        attempts = 3
    return {"enabled": bool(enabled), "max_attempts": attempts}


@router.get("/overview")
async def overview():
    settings = _settings()
    roster = _specialists_module().load_specialists(settings.get("specialists", []))
    jobs = list(reversed(_jobs()))[:50]
    keys = ("id", "kind", "specialist", "title", "status", "created_at", "attempts", "max_attempts",
            "review_verdict", "last_feedback", "reason")
    slim = [{k: j.get(k) for k in keys} for j in jobs]
    return {
        "team": [{"id": s["id"], "title": s["title"], "focus": s["focus"]} for s in roster],
        "jobs": slim,
        "needs_human": sum(1 for j in slim if j.get("kind") == "local" and j.get("status") == "needs_human"),
        "loop": _loop(settings),
        "bridge": {"webhook_configured": _webhook_set(),
                   "inbox_dir": settings.get("grokbot_inbox_dir") or ""},
    }
