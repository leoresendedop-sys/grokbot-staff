"""Tool handlers. Every handler: (args: dict, **kwargs) -> str (JSON); never raises."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from . import bridge, harness, pipeline, specialists
from .store import JobStore, new_id, now_iso

logger = logging.getLogger(__name__)
_MAX_TASK = 20_000


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _text(args: Dict[str, Any], key: str, limit: int = _MAX_TASK) -> str:
    v = args.get(key)
    return str(v).strip()[:limit] if v is not None else ""


class Staff:
    """Holds plugin collaborators so handlers are testable without Hermes."""

    def __init__(self, *, get_config: Callable[[str, Any], Any],
                 get_secret: Callable[[str], Optional[str]],
                 store: JobStore, llm: Any = None,
                 opener: Any = None):
        self.get_config = get_config
        self.get_secret = get_secret
        self.store = store
        self.llm = llm
        self.opener = opener

    # ---- settings -------------------------------------------------------
    def roster(self):
        return specialists.load_specialists(self.get_config("specialists", []))

    def _int(self, key: str, default: int) -> int:
        try:
            return int(self.get_config(key, default) or default)
        except (TypeError, ValueError):
            return default

    def webhook_url(self) -> str:
        try:
            return (self.get_secret("GROKBOT_STAFF_WEBHOOK_URL") or "").strip()
        except Exception:  # secret backend unavailable
            return ""

    def inbox_dir(self) -> str:
        return str(self.get_config("grokbot_inbox_dir", "") or "").strip()

    # ---- local staff ----------------------------------------------------
    def _bool(self, key: str, default: bool) -> bool:
        v = self.get_config(key, default)
        if isinstance(v, str):
            return v.strip().lower() not in ("0", "false", "no", "off", "")
        return bool(default if v is None else v)

    def max_attempts(self) -> int:
        return max(1, min(self._int("max_attempts", pipeline.DEFAULT_MAX_ATTEMPTS), 10))

    def staff_assign(self, args: Dict[str, Any], **kwargs) -> str:
        try:
            if not isinstance(args, dict):
                args = {}
            resume = _text(args, "resume_job_id", 64)
            if resume:
                return self._resume(resume, args)
            roster = self.roster()
            spec = specialists.find(roster, _text(args, "specialist", 40))
            if not spec:
                return _j({"ok": False, "error": "unknown specialist",
                           "available": [s["id"] for s in roster]})
            contract = harness.parse_contract(args)
            if not contract["task"]:
                return _j({"ok": False, "error": "task is required"})
            if self.llm is None:
                return _j({"ok": False, "error": "host LLM access (ctx.llm) is not available"})
            loop = self._bool("quality_loop", True)
            job = self.store.add({
                "id": new_id("staff"), "kind": "local", "status": "running",
                "specialist": spec["id"], "title": _text(args, "title", 120) or contract["task"][:80],
                "task": contract["task"], "created_at": now_iso(),
                "conversation_id": str(kwargs.get("conversation_id") or ""),
                "loop": loop, "attempts": 0, "max_attempts": self.max_attempts() if loop else 1,
            })
            files = harness.JobFiles(self.store.dir, job["id"])
            files.write_contract(contract)
            files.write_progress(harness.new_progress(contract["task"]))
            if not loop:
                return self._single_shot(job, spec, roster, files)
            return self._run(job, spec, roster, files, self.max_attempts())
        except Exception as e:
            logger.exception("staff_assign failed")
            return _j({"ok": False, "error": f"internal error: {type(e).__name__}"})

    def _resume(self, jid: str, args: Dict[str, Any]) -> str:
        job = self.store.get(jid)
        if not job or job.get("kind") != "local":
            return _j({"ok": False, "error": "job not found"})
        if job.get("status") == "done":
            return _j({"ok": False, "job_id": jid, "error": "job is already done"})
        if self.llm is None:
            return _j({"ok": False, "error": "host LLM access (ctx.llm) is not available"})
        files = harness.JobFiles(self.store.dir, jid)
        if not files.exists():
            return _j({"ok": False, "job_id": jid, "error": "job has no contract file (pre-0.2 job)"})
        extra = harness.parse_contract({"done_when": args.get("done_when"),
                                        "constraints": args.get("constraints"),
                                        "context": args.get("context")})
        c = files.contract() or {}
        if extra["done_when"]:
            c.update(done_when=extra["done_when"], done_when_source="caller")
        c["constraints"] = (c.get("constraints") or []) + extra["constraints"]
        c["inputs"] = (c.get("inputs") or []) + extra["inputs"]
        files.write_contract(c)
        roster = self.roster()
        spec = specialists.find(roster, job.get("specialist", "")) or specialists.find(roster, "chief")
        self.store.update(jid, status="running")
        return self._run(job, spec, roster, files, self.max_attempts())

    def _run(self, job, spec, roster, files, max_attempts: int) -> str:
        jid = job["id"]
        pipe = pipeline.Pipeline(self.llm, self._int("max_output_tokens", 1200),
                                 log=lambda **kw: self.store.update(jid, **kw))
        try:
            res = pipe.run(files, spec, roster, max_attempts)
        except Exception as e:  # provider errors must not escape
            self.store.update(jid, status="failed", error=f"{type(e).__name__}: {e}"[:500])
            return _j({"ok": False, "job_id": jid, "error": f"LLM call failed: {type(e).__name__}"})
        review = res.get("review") or {}
        self.store.update(jid, status=res["status"], reason=res["reason"], attempts=res["attempts"],
                          output=res["deliverable"], review_verdict=review.get("verdict"),
                          done_when=len((res.get("contract") or {}).get("done_when") or []))
        out = {"ok": True, "job_id": jid, "specialist": spec["id"], "title": job["title"],
               "status": res["status"], "done": res["status"] == "done", "reason": res["reason"],
               "attempts": res["attempts"], "done_when": (res.get("contract") or {}).get("done_when"),
               "done_when_source": (res.get("contract") or {}).get("done_when_source"),
               "review": review or None, "output": res["deliverable"]}
        if res["status"] == "needs_human":
            out["history"] = self._history(files)
            out["next"] = (f"Not delivered: {res['reason']}. Tighten the contract or inputs, then call "
                           f"staff_assign with resume_job_id={jid}.")
        return _j(out)

    def _single_shot(self, job, spec, roster, files) -> str:
        """quality_loop=false: the 0.1 behaviour (one answer, no verification)."""
        c = files.contract() or {}
        user = c["task"] if not c.get("inputs") else c["task"] + "\n\nContext:\n" + "\n".join(c["inputs"])
        try:
            res = self.llm.complete(
                messages=[{"role": "system", "content": specialists.system_prompt(spec, roster)},
                          {"role": "user", "content": user}],
                max_tokens=self._int("max_output_tokens", 1200), purpose=f"grokbot-staff.{spec['id']}")
            output = str(getattr(res, "text", "") or "")
        except Exception as e:
            self.store.update(job["id"], status="failed", error=f"{type(e).__name__}: {e}"[:500])
            return _j({"ok": False, "job_id": job["id"], "error": f"LLM call failed: {type(e).__name__}"})
        files.write_deliverable(output)
        self.store.update(job["id"], status="done", output=output, attempts=1, reason="quality loop off")
        return _j({"ok": True, "job_id": job["id"], "specialist": spec["id"], "title": job["title"],
                   "status": "done", "done": True, "verified": False, "output": output})

    @staticmethod
    def _history(files) -> List[Dict[str, Any]]:
        out = []
        for a in files.attempts():
            fb = a.get("feedback") or {}
            out.append({"attempt": a.get("n"), "stopped": a.get("stopped"),
                        "verified": fb.get("pass") if fb else None,
                        "failed": fb.get("failed"), "expected": fb.get("expected"),
                        "observed": fb.get("observed"), "scope": fb.get("scope"),
                        "dead_ends": fb.get("dead_ends"),
                        "review": (a.get("review") or {}).get("verdict"),
                        "deliverable_preview": (a.get("deliverable") or "")[:400]})
        return out

    def staff_status(self, args: Dict[str, Any], **kwargs) -> str:
        try:
            if not isinstance(args, dict):
                args = {}
            jid = _text(args, "job_id", 64)
            if jid:
                job = self.store.get(jid)
                if not job:
                    return _j({"ok": False, "error": "job not found"})
                detail = dict(job)
                try:
                    files = harness.JobFiles(self.store.dir, jid)
                except ValueError:
                    files = None
                if files is not None and files.exists():
                    detail.update(contract=files.contract(), progress=files.progress(),
                                  history=self._history(files), review=files.review())
                    if files.deliverable():
                        detail["output"] = files.deliverable()
                return _j({"ok": True, "job": detail})
            limit = self._int_arg(args, "limit", 10)
            keys = ("id", "specialist", "title", "status", "attempts", "max_attempts",
                    "review_verdict", "last_feedback", "created_at")
            jobs = [{k: j.get(k) for k in keys} for j in self.store.list(kind="local", limit=limit)]
            return _j({"ok": True,
                       "team": [{"id": s["id"], "title": s["title"], "focus": s["focus"]} for s in self.roster()],
                       "jobs": jobs,
                       "needs_human": [j["id"] for j in jobs if j.get("status") == "needs_human"],
                       "loop": {"enabled": self._bool("quality_loop", True), "max_attempts": self.max_attempts()},
                       "bridge": self._bridge_state()})
        except Exception as e:
            return _j({"ok": False, "error": f"internal error: {type(e).__name__}"})

    @staticmethod
    def _int_arg(args, key, default):
        try:
            return max(1, min(int(args.get(key) or default), 200))
        except (TypeError, ValueError):
            return default

    # ---- Grok Bot bridge -------------------------------------------------
    def _bridge_state(self) -> Dict[str, Any]:
        url = self.webhook_url()
        return {"webhook": bridge.redact_url(url) if url else None,
                "inbox_dir": self.inbox_dir() or None}

    def grokbot_delegate(self, args: Dict[str, Any], **kwargs) -> str:
        try:
            task = _text(args, "task")
            if not task:
                return _j({"ok": False, "error": "task is required"})
            url, inbox = self.webhook_url(), self.inbox_dir()
            if not url and not inbox:
                return _j({"ok": False, "error": "not_configured", "how_to_fix": bridge.SETUP_HINT})
            sid = _text(args, "specialist", 40)
            spec = specialists.find(self.roster(), sid) if sid else None
            if sid and not spec:
                return _j({"ok": False, "error": "unknown specialist",
                           "available": [s["id"] for s in self.roster()]})
            payload = {
                "id": new_id("gbs"),
                "created_at": now_iso(),
                "specialist": spec["id"] if spec else "chief",
                "title": _text(args, "title", 120) or task[:80],
                "task": task,
                "reply_hint": _text(args, "reply_hint", 1000) or (
                    "Reply with the result. If you can write files, save it as "
                    "results/<id>.md in the shared inbox folder; otherwise the user will paste it back."),
            }
            delivery: Dict[str, Any] = {}
            if url:
                ok, msg = bridge.post_webhook(url, payload, self._int("webhook_timeout_seconds", 15),
                                              opener=self.opener)
                delivery["webhook"] = {"ok": ok, "detail": msg, "target": bridge.redact_url(url)}
            if inbox:
                ok, msg = bridge.write_inbox_handoff(inbox, payload)
                delivery["inbox"] = {"ok": ok, "detail": msg}
            sent = any(d["ok"] for d in delivery.values())
            self.store.add({**payload, "kind": "grokbot", "status": "pending" if sent else "failed",
                            "delivery": delivery})
            out = {"ok": sent, "job_id": payload["id"], "status": "pending" if sent else "failed",
                   "delivery": delivery}
            if sent:
                out["next"] = f"Call grokbot_collect with job_id={payload['id']} when Grok Bot has answered."
            return _j(out)
        except Exception as e:
            logger.exception("grokbot_delegate failed")
            return _j({"ok": False, "error": f"internal error: {type(e).__name__}"})

    def grokbot_collect(self, args: Dict[str, Any], **kwargs) -> str:
        try:
            jid = _text(args, "job_id", 64)
            job = self.store.get(jid) if jid else None
            if not job or job.get("kind") != "grokbot":
                return _j({"ok": False, "error": "Grok Bot job not found"})
            pasted = _text(args, "result", 200_000)
            source = "pasted" if pasted else "inbox"
            result = pasted or bridge.read_inbox_result(self.inbox_dir(), jid)
            if not result:
                return _j({"ok": False, "job_id": jid, "status": job.get("status"),
                           "error": "no result yet",
                           "hint": "Ask your Grok Bot to save results/<job_id>.md in the inbox folder, "
                                   "or call grokbot_collect again with result=<pasted text>."})
            self.store.update(jid, status="done", result=result, result_source=source,
                              completed_at=now_iso())
            return _j({"ok": True, "job_id": jid, "status": "done", "source": source, "result": result})
        except Exception as e:
            return _j({"ok": False, "error": f"internal error: {type(e).__name__}"})

    def grokbot_status(self, args: Dict[str, Any], **kwargs) -> str:
        try:
            st = _text(args, "status", 10) or "all"
            jobs = self.store.list(kind="grokbot", status=None if st == "all" else st,
                                   limit=self._int_arg(args, "limit", 20))
            return _j({"ok": True, "bridge": self._bridge_state(),
                       "jobs": [{k: j.get(k) for k in ("id", "specialist", "title", "status", "created_at")}
                                for j in jobs]})
        except Exception as e:
            return _j({"ok": False, "error": f"internal error: {type(e).__name__}"})

    # ---- slash commands -------------------------------------------------
    def slash_staff(self, raw: str = "", **kwargs) -> str:
        raw = (raw or "").strip()
        if not raw or raw in ("list", "team"):
            return "\n".join(f"- {s['id']}: {s['title']} — {s['focus']}" for s in self.roster()) + \
                "\nUsage: /staff <specialist> <task> | /staff status [job_id] | /staff resume <job_id>"
        if raw.startswith("status"):
            return self.staff_status({"job_id": raw[6:].strip()})
        if raw.startswith("resume"):
            return self.staff_assign({"resume_job_id": raw[6:].strip()}, **kwargs)
        sid, _, task = raw.partition(" ")
        return self.staff_assign({"specialist": sid, "task": task}, **kwargs)

    def slash_grokbot(self, raw: str = "", **kwargs) -> str:
        raw = (raw or "").strip()
        if not raw or raw == "status":
            return self.grokbot_status({})
        if raw.startswith("collect"):
            jid, _, res = raw[7:].strip().partition(" ")
            return self.grokbot_collect({"job_id": jid, "result": res})
        return self.grokbot_delegate({"task": raw})
