"""Loop + Graph: Production -> Verification -> Review -> Delivery.

- Production: the specialist answers the contract (deliverable + optional author notes).
- Verification: a separate LLM call checks the deliverable against ``done_when`` and
  returns structured feedback (failed, expected, observed, scope, dead_ends).
  Self-report ("I tested it", "all criteria met") is never evidence.
- Review: an independent LLM call that sees only the contract and the deliverable,
  never the author's notes, system prompt, attempt history or verifier feedback.
- Delivery: only an approved deliverable is marked ``done``. When attempts run out,
  or the specialist repeats itself, the job becomes ``needs_human`` with its history.

Every stage reads its inputs from the job files (see ``harness.py``).
"""
from __future__ import annotations

import difflib
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import specialists
from .harness import JobFiles, new_progress, render_contract

NOTES_MARKER = re.compile(r"^\s{0,3}#{1,6}\s*author notes\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
SIMILARITY_STOP = 0.97
DEFAULT_MAX_ATTEMPTS = 3

PRODUCTION_RULES = (
    "You are working against a task contract. Produce the DELIVERABLE so that every "
    "DONE WHEN criterion is visibly satisfied by the deliverable itself. Respect every "
    "constraint. If anything about your process matters (assumptions, what you skipped), "
    "put it after a final heading '## Author notes'. Author notes are never shown to the "
    "verifier or the reviewer, so the deliverable must stand on its own."
)

CONTRACT_DRAFT = (
    "You are the chief of staff drafting the acceptance criteria for a task contract. "
    "Write 2-5 'done when' criteria that a separate checker can verify by reading the "
    "deliverable alone (concrete, observable, no 'high quality' or 'good'). "
    "Return ONLY a JSON array of strings."
)

VERIFIER = (
    "You are a strict verifier. You receive a task contract and a deliverable. Check the "
    "deliverable against EVERY 'done when' criterion and every constraint. Judge only what "
    "is present in the deliverable. Claims such as 'I verified', 'tested', 'all criteria are "
    "met' or 'done' are self-report and are NOT evidence: a criterion holds only if the "
    "deliverable itself shows it.\n"
    "Return ONLY a JSON object with keys: "
    '"pass" (true only if every criterion holds), '
    '"failed" (list of the criteria that do not hold, verbatim), '
    '"expected" (what a passing deliverable would show), '
    '"observed" (what the deliverable actually shows), '
    '"scope" (the smallest part that must change), '
    '"dead_ends" (list of approaches in this deliverable that should not be repeated).'
)

REVIEWER = (
    "You are an independent reviewer. You see only the task contract and the final "
    "deliverable; you know nothing about how it was produced. Decide whether it should be "
    "delivered to the user as is. Self-report inside the deliverable is not evidence.\n"
    "Return ONLY a JSON object with keys: "
    '"verdict" ("approve" or "revise"), "reasons" (list of short strings), '
    '"required_changes" (list; empty when approving).'
)


# ---- helpers -------------------------------------------------------------
def _text(res: Any) -> str:
    return str(getattr(res, "text", "") or "")


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in a reply (handles ``` fences and prose)."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    candidates = [m.group(1)] if m else []
    candidates.append(text)
    for c in candidates:
        c = c.strip()
        try:
            return json.loads(c)
        except ValueError:
            pass
        for opener, closer in (("{", "}"), ("[", "]")):
            i, j = c.find(opener), c.rfind(closer)
            if 0 <= i < j:
                try:
                    return json.loads(c[i:j + 1])
                except ValueError:
                    continue
    return None


def split_notes(output: str) -> Tuple[str, str]:
    """Return (deliverable, author_notes)."""
    m = NOTES_MARKER.search(output or "")
    if not m:
        return (output or "").strip(), ""
    return output[:m.start()].strip(), output[m.end():].strip()


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", (text or "").lower()).split())


def near_identical(a: str, b: str, threshold: float = SIMILARITY_STOP) -> bool:
    na, nb = normalize(a)[:20_000], normalize(b)[:20_000]
    if na == nb:
        return True
    if not na or not nb:
        return False
    sm = difflib.SequenceMatcher(None, na, nb, autojunk=False)
    return sm.quick_ratio() >= threshold and sm.ratio() >= threshold


def _strs(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x)[:1000] for x in v if str(x).strip()][:20]
    s = str(v).strip()
    return [s[:1000]] if s else []


def parse_feedback(raw: str, done_when: List[str]) -> Dict[str, Any]:
    """Structured verifier feedback. Anything unparsable fails closed."""
    data = extract_json(raw)
    if not isinstance(data, dict):
        return {"pass": False, "failed": list(done_when), "expected": "a JSON verdict from the verifier",
                "observed": "verifier reply was not valid JSON", "scope": "verification",
                "dead_ends": [], "parse_error": True}
    failed = _strs(data.get("failed"))
    passed = data.get("pass") is True and not failed
    return {"pass": passed, "failed": failed,
            "expected": str(data.get("expected") or "")[:2000],
            "observed": str(data.get("observed") or "")[:2000],
            "scope": str(data.get("scope") or "")[:1000],
            "dead_ends": _strs(data.get("dead_ends"))}


def parse_review(raw: str) -> Dict[str, Any]:
    data = extract_json(raw)
    if not isinstance(data, dict):
        return {"verdict": "revise", "reasons": ["reviewer reply was not valid JSON"],
                "required_changes": [], "parse_error": True}
    verdict = "approve" if str(data.get("verdict", "")).strip().lower() == "approve" else "revise"
    changes = _strs(data.get("required_changes"))
    if verdict == "approve" and changes:
        verdict = "revise"
    return {"verdict": verdict, "reasons": _strs(data.get("reasons")), "required_changes": changes}


def reviewer_messages(contract: Dict[str, Any], deliverable: str) -> List[Dict[str, str]]:
    """The reviewer's whole world: contract + deliverable. Nothing else, by construction."""
    return [{"role": "system", "content": REVIEWER},
            {"role": "user", "content": f"{render_contract(contract)}\n\n=== DELIVERABLE ===\n{deliverable}"}]


def verifier_messages(contract: Dict[str, Any], deliverable: str) -> List[Dict[str, str]]:
    return [{"role": "system", "content": VERIFIER},
            {"role": "user", "content": f"{render_contract(contract)}\n\n=== DELIVERABLE ===\n{deliverable}"}]


def _feedback_block(progress: Dict[str, Any], last: Optional[Dict[str, Any]]) -> str:
    if not last:
        return ""
    fb = last.get("feedback") or {}
    rv = last.get("review") or {}
    lines = ["", "=== FEEDBACK ON YOUR PREVIOUS ATTEMPT (fix this; do not repeat it) ==="]
    if fb and not fb.get("pass"):
        lines += [f"Failed criteria: {'; '.join(fb.get('failed') or []) or '(unspecified)'}",
                  f"Expected: {fb.get('expected', '')}", f"Observed: {fb.get('observed', '')}",
                  f"Scope of change: {fb.get('scope', '')}"]
    if rv and rv.get("verdict") == "revise":
        lines += ["Independent review asked for changes: " +
                  "; ".join((rv.get("required_changes") or []) + (rv.get("reasons") or []))]
    never = progress.get("never") or []
    if never:
        lines.append("Never again: " + "; ".join(never))
    return "\n".join(lines)


# ---- the pipeline --------------------------------------------------------
class Pipeline:
    def __init__(self, llm: Any, max_tokens: int, log: Optional[Callable[..., Any]] = None):
        self.llm = llm
        self.max_tokens = max_tokens
        self.log = log or (lambda **kw: None)  # mirrors summary fields into jobs.json

    def _call(self, messages, purpose: str, temperature: Optional[float] = None) -> str:
        kw: Dict[str, Any] = {"max_tokens": self.max_tokens, "purpose": purpose}
        if temperature is not None:
            kw["temperature"] = temperature
        return _text(self.llm.complete(messages=messages, **kw))

    def draft_done_when(self, files: JobFiles, roster: List[Dict[str, str]]) -> Dict[str, Any]:
        c = files.contract() or {}
        if c.get("done_when"):
            return c
        chief = specialists.find(roster, "chief") or {"id": "chief", "title": "Chief of staff",
                                                     "focus": "", "prompt": ""}
        raw = self._call([{"role": "system", "content": specialists.system_prompt(chief, roster) +
                           "\n\n" + CONTRACT_DRAFT},
                          {"role": "user", "content": render_contract({**c, "done_when": []})}],
                         "grokbot-staff.contract", temperature=0)
        data = extract_json(raw)
        crit = [str(x).strip()[:500] for x in data if str(x).strip()][:5] if isinstance(data, list) else []
        source = "chief"
        if not crit:
            crit = [f"The deliverable directly and completely addresses the task: {c.get('task', '')[:300]}",
                    "Every constraint listed in the contract is respected",
                    "The deliverable ends with a 'Next steps' list"]
            source = "fallback"
        c.update(done_when=crit, done_when_source=source)
        files.write_contract(c)
        return c

    def run(self, files: JobFiles, spec: Dict[str, str], roster: List[Dict[str, str]],
            max_attempts: int) -> Dict[str, Any]:
        contract = self.draft_done_when(files, roster)
        progress = files.progress() or new_progress(contract.get("task", ""))
        history = files.attempts()
        first = len(history) + 1
        status, reason = "needs_human", "attempts exhausted"
        for n in range(first, first + max(1, max_attempts)):
            # ---- Production (reads contract + progress from files)
            contract = files.contract() or contract
            progress = files.progress() or progress
            last = history[-1] if history else None
            user = render_contract(contract) + _feedback_block(progress, last)
            raw = self._call([{"role": "system", "content": specialists.system_prompt(spec, roster) +
                               "\n\n" + PRODUCTION_RULES},
                              {"role": "user", "content": user}], f"grokbot-staff.{spec['id']}")
            deliverable, notes = split_notes(raw)
            attempt: Dict[str, Any] = {"n": n, "deliverable": deliverable, "author_notes": notes}

            if any(near_identical(deliverable, h.get("deliverable", "")) for h in history):
                attempt["stopped"] = "identical_output"
                files.write_attempt(n, attempt)
                history.append(attempt)
                progress["tried"].append(f"attempt {n}: repeated a previous attempt")
                progress["learned"].append("the specialist is stuck; more retries will not help")
                progress["next"] = "a human should refine the contract or the inputs"
                files.write_progress(progress)
                status, reason = "needs_human", "identical output to a previous attempt"
                self.log(attempts=n, last_feedback="identical output: stopped early")
                break

            # ---- Verification (contract + deliverable only)
            if not deliverable:
                fb = {"pass": False, "failed": list(contract["done_when"]), "expected": "a deliverable",
                      "observed": "empty deliverable", "scope": "everything", "dead_ends": []}
            else:
                fb = parse_feedback(self._call(verifier_messages(contract, deliverable),
                                               "grokbot-staff.verify", temperature=0),
                                    contract["done_when"])
            attempt["feedback"] = fb

            # ---- Review (independent; contract + deliverable only)
            if fb["pass"]:
                files.write_deliverable(deliverable)
                review = parse_review(self._call(reviewer_messages(files.contract() or contract,
                                                                   files.deliverable()),
                                                 "grokbot-staff.review", temperature=0))
                attempt["review"] = review
                files.write_review({**review, "attempt": n})

            files.write_attempt(n, attempt)
            history.append(attempt)
            review = attempt.get("review") or {}
            if fb["pass"] and review.get("verdict") == "approve":
                progress["tried"].append(f"attempt {n}: passed verification and review")
                progress["next"] = "delivered"
                files.write_progress(progress)
                status, reason = "done", "verified and approved"
                self.log(attempts=n, last_feedback="passed", review_verdict="approve")
                break

            # ---- feed the failure back into the next attempt
            if not fb["pass"]:
                progress["tried"].append(f"attempt {n}: failed {len(fb['failed'])} criteria")
                if fb.get("observed"):
                    progress["learned"].append(fb["observed"][:500])
                for d in fb.get("dead_ends") or []:
                    if d not in progress["never"]:
                        progress["never"].append(d)
                progress["next"] = f"fix: {fb.get('scope') or '; '.join(fb['failed'])}"[:500]
                summary = "failed: " + "; ".join(fb["failed"])[:300]
            else:
                progress["tried"].append(f"attempt {n}: passed verification, review asked for changes")
                progress["learned"].extend(review.get("reasons") or [])
                progress["next"] = ("apply review: " + "; ".join(review.get("required_changes") or
                                                                  review.get("reasons") or []))[:500]
                summary = "review: revise"
            files.write_progress(progress)
            self.log(attempts=n, last_feedback=summary, review_verdict=review.get("verdict"))

        if status != "done":
            best = history[-1]["deliverable"] if history else ""
            files.write_deliverable(best)
            progress["next"] = progress.get("next") or "a human should decide"
            files.write_progress(progress)
        return {"status": status, "reason": reason, "attempts": len(history),
                "deliverable": files.deliverable(), "contract": files.contract(),
                "progress": files.progress(), "review": files.review()}

