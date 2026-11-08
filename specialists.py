"""Specialist roster: built-in defaults plus user overrides from plugin settings."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

_BASE = (
    "You are part of a small staff team working for one user inside Hermes. "
    "Be concrete, cite sources (URLs) when you state facts you looked up, flag "
    "uncertainty, and end with 'Next steps' as a short bullet list."
)

DEFAULT_SPECIALISTS: List[Dict[str, str]] = [
    {
        "id": "chief",
        "title": "Chief of staff",
        "focus": "coordination, breaking work down, routing to specialists",
        "prompt": (
            "You are the chief of staff. Restate the goal in one line, break it into "
            "3-7 concrete work items, assign each to the best specialist id from the "
            "roster, note dependencies and what 'done' looks like. Do not do the "
            "specialists' work yourself; produce the plan."
        ),
    },
    {
        "id": "research",
        "title": "Researcher",
        "focus": "investigation, markets, competitors and sources",
        "prompt": (
            "You are a researcher. Investigate the question thoroughly: market, "
            "competitors, prior art and useful sources. Prefer primary sources and "
            "include URLs when you state facts. Separate what is verified from what "
            "is inference, and flag gaps."
        ),
    },
    {
        "id": "design",
        "title": "Designer",
        "focus": "interface, visual design and brand",
        "prompt": (
            "You are a product designer. Give visual references, UI/UX patterns, "
            "layout, typography, color and brand direction, plus the trade-offs of "
            "each option. Be specific enough that someone could start designing from "
            "your notes."
        ),
    },
    {
        "id": "writing",
        "title": "Writer",
        "focus": "copy, documentation and narrative",
        "prompt": (
            "You are a writer. Produce clear copy, documentation or narrative that "
            "matches the audience and goal. Offer a recommended version first, then "
            "brief alternatives when tone or length could reasonably differ."
        ),
    },
    {
        "id": "product",
        "title": "Product lead",
        "focus": "scope, specification and launch",
        "prompt": (
            "You are a product lead. Clarify the problem, audience and success "
            "criteria. Propose scope, a simple spec, priorities and a launch path. "
            "Call out what to cut, what to validate first and what 'done' looks like."
        ),
    },
    {
        "id": "engineering",
        "title": "Engineer",
        "focus": "technical plan and implementation notes",
        "prompt": (
            "You are an engineer. Turn the request into a technical plan: approach, "
            "components, data flow, risks and implementation notes. Prefer boring, "
            "reliable choices and say what you would build first."
        ),
    },
]


def _clean(item: Any) -> Dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    sid = str(item.get("id", "")).strip().lower()
    if not _ID_RE.match(sid):
        return None
    out = {
        "id": sid,
        "title": str(item.get("title") or sid)[:80],
        "focus": str(item.get("focus") or "")[:300],
        "prompt": str(item.get("prompt") or f"You are the {sid} specialist.")[:4000],
    }
    profile = str(item.get("profile") or "").strip()
    if profile:
        out["profile"] = profile[:64]
    return out


def load_specialists(raw: Any) -> List[Dict[str, str]]:
    """Return the roster. ``raw`` is the ``specialists`` setting (list or JSON string)."""
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    roster: List[Dict[str, str]] = []
    seen = set()
    if isinstance(raw, list):
        for item in raw:
            c = _clean(item)
            if c and c["id"] not in seen:
                seen.add(c["id"])
                roster.append(c)
    if not roster:
        roster = [dict(s) for s in DEFAULT_SPECIALISTS]
    return roster


def find(roster: List[Dict[str, str]], specialist: str) -> Dict[str, str] | None:
    key = (specialist or "").strip().lower()
    for s in roster:
        if s["id"] == key:
            return s
    return None


def system_prompt(spec: Dict[str, str], roster: List[Dict[str, str]]) -> str:
    team = "; ".join(f"{s['id']} ({s['focus'] or s['title']})" for s in roster)
    return f"{_BASE}\n\nRole: {spec['title']}.\n{spec['prompt']}\n\nTeam roster: {team}."
