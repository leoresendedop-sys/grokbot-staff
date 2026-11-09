"""Harness: the task contract and the per-job files that survive a session restart.

A job lives in ``<data_dir>/jobs/<job_id>/``:

- ``contract.json``  task, inputs, deliverable, constraints, done_when (+ where done_when came from)
- ``progress.json``  goal, tried, learned, next, never (the playbook's progress file)
- ``attempt-<n>.json`` one production attempt: deliverable, author notes, verifier feedback
- ``review.json``    the independent reviewer's verdict
- ``deliverable.md`` the delivered (or best, when a human is needed) deliverable

Pipeline stages read their inputs from these files, not from chat memory.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_ITEM = 2000
_MAX_ITEMS = 20

CONTRACT_FIELDS = ("task", "inputs", "deliverable", "constraints", "done_when")


def _as_list(value: Any) -> List[str]:
    """Accept a list, a JSON list string, or newline / bullet separated text."""
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    value = parsed
            except ValueError:
                pass
        if isinstance(value, str):
            value = [ln for ln in re.split(r"\r?\n|;\s+", s)]
    if not isinstance(value, (list, tuple)):
        value = [value]
    out: List[str] = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("criterion") or item.get("text") or json.dumps(item, ensure_ascii=False)
        t = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", str(item)).strip()
        if t:
            out.append(t[:_MAX_ITEM])
    return out[:_MAX_ITEMS]


def parse_contract(args: Dict[str, Any]) -> Dict[str, Any]:
    """Build a contract from tool args.

    Accepts a nested ``contract`` object and/or top-level fields; top-level wins.
    The legacy ``context`` argument becomes an input. ``done_when`` stays empty when
    the caller did not give one, so the chief drafts it.
    """
    nested = args.get("contract")
    if isinstance(nested, str) and nested.strip().startswith("{"):
        try:
            nested = json.loads(nested)
        except ValueError:
            nested = None
    src: Dict[str, Any] = dict(nested) if isinstance(nested, dict) else {}
    for k in CONTRACT_FIELDS:
        if args.get(k) not in (None, "", []):
            src[k] = args[k]
    task = str(src.get("task") or "").strip()[:20_000]
    inputs = _as_list(src.get("inputs"))
    ctx = str(args.get("context") or "").strip()
    if ctx:
        inputs.append(ctx[:20_000])
    done_when = _as_list(src.get("done_when"))
    return {
        "task": task,
        "inputs": inputs,
        "deliverable": str(src.get("deliverable") or "").strip()[:_MAX_ITEM],
        "constraints": _as_list(src.get("constraints")),
        "done_when": done_when,
        "done_when_source": "caller" if done_when else "",
    }


def render_contract(c: Dict[str, Any]) -> str:
    """Plain-text contract as every stage sees it."""
    def block(title: str, items: List[str]) -> str:
        return f"{title}:\n" + ("\n".join(f"- {i}" for i in items) if items else "- (none)")
    parts = [f"TASK:\n{c.get('task', '')}"]
    parts.append(block("INPUTS", c.get("inputs") or []))
    parts.append(f"DELIVERABLE:\n{c.get('deliverable') or '(the answer to the task)'}")
    parts.append(block("CONSTRAINTS", c.get("constraints") or []))
    parts.append("DONE WHEN (every criterion must hold):\n" +
                 "\n".join(f"{n}. {d}" for n, d in enumerate(c.get("done_when") or [], 1)))
    return "\n\n".join(parts)


def new_progress(goal: str) -> Dict[str, Any]:
    return {"goal": goal, "tried": [], "learned": [], "next": "produce attempt 1", "never": []}


class JobFiles:
    """Files of one job. ``root`` is the plugin data dir."""

    def __init__(self, root: Path, job_id: str):
        if not _SAFE_ID.match(job_id or ""):
            raise ValueError("invalid job id")
        self.dir = Path(root) / "jobs" / job_id

    def _write(self, name: str, data: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        f = self.dir / name
        tmp = f.with_name(f.name + ".tmp")
        if isinstance(data, str):
            tmp.write_text(data, encoding="utf-8")
        else:
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)

    def _read(self, name: str) -> Any:
        try:
            text = (self.dir / name).read_text(encoding="utf-8")
        except OSError:
            return None
        if name.endswith(".json"):
            try:
                return json.loads(text)
            except ValueError:
                return None
        return text

    def exists(self) -> bool:
        return (self.dir / "contract.json").is_file()

    # contract / progress
    def write_contract(self, c: Dict[str, Any]) -> None:
        self._write("contract.json", c)

    def contract(self) -> Optional[Dict[str, Any]]:
        return self._read("contract.json")

    def write_progress(self, p: Dict[str, Any]) -> None:
        self._write("progress.json", p)

    def progress(self) -> Optional[Dict[str, Any]]:
        return self._read("progress.json")

    # attempts
    def write_attempt(self, n: int, a: Dict[str, Any]) -> None:
        self._write(f"attempt-{n}.json", a)

    def attempts(self) -> List[Dict[str, Any]]:
        out = []
        n = 1
        while True:
            a = self._read(f"attempt-{n}.json")
            if a is None:
                break
            out.append(a)
            n += 1
        return out

    # deliverable / review
    def write_deliverable(self, text: str) -> None:
        self._write("deliverable.md", text)

    def deliverable(self) -> str:
        return self._read("deliverable.md") or ""

    def write_review(self, r: Dict[str, Any]) -> None:
        self._write("review.json", r)

    def review(self) -> Optional[Dict[str, Any]]:
        return self._read("review.json")
