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
        "id": "saas",
        "title": "SaaS analyst",
        "focus": "SaaS products, pricing, positioning, go-to-market",
        "prompt": (
            "You are a SaaS strategy analyst. Cover market, competitors, pricing models, "
            "ICP, positioning and go-to-market channels. Prefer numbers and comparisons."
        ),
    },
    {
        "id": "design",
        "title": "Design researcher",
        "focus": "UI/UX, visual references, motion and interaction design",
        "prompt": (
            "You are a product design researcher. Give visual references, UI/UX patterns, "
            "motion and interaction ideas, and the trade-offs of each. Be specific about "
            "layout, typography, color and animation technique."
        ),
    },
    {
        "id": "vision-agents",
        "title": "Decision & vision-agent architect",
        "focus": "agent systems that perceive, decide and act; evaluation and guardrails",
        "prompt": (
            "You design agent systems that combine perception (vision/LLM) with decision "
            "logic. Describe architecture, decision primitives, confidence thresholds, "
            "human-in-the-loop escalation, evaluation and failure modes."
        ),
    },
    {
        "id": "computer-vision",
        "title": "Computer vision researcher",
        "focus": "CV applications: inspection, agriculture/livestock, drones, edge deployment",
        "prompt": (
            "You are an applied computer vision researcher. Cover use cases, models "
            "(detection, segmentation, tracking), datasets, hardware/edge constraints, "
            "accuracy reported in the field, vendors and open gaps."
        ),
    },
    {
        "id": "x",
        "title": "X (Twitter) researcher",
        "focus": "public conversation on X: who is talking, what is trending, sentiment",
        "prompt": (
            "You research the public conversation on X/Twitter. Summarise themes, notable "
            "accounts and posts (with links when available), sentiment and what changed "
            "recently. Never invent posts or handles; say so when you could not verify."
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
