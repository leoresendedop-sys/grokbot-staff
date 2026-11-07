"""Tool handlers. Every handler: (args: dict, **kwargs) -> str (JSON); never raises."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional

from . import bridge, specialists
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
    def staff_assign(self, args: Dict[str, Any], **kwargs) -> str:
        try:
            roster = self.roster()
            spec = specialists.find(roster, _text(args, "specialist", 40))
            task = _text(args, "task")
            if not spec:
                return _j({"ok": False, "error": "unknown specialist",
                           "available": [s["id"] for s in roster]})
            if not task:
                return _j({"ok": False, "error": "task is required"})
            if self.llm is None:
                return _j({"ok": False, "error": "host LLM access (ctx.llm) is not available"})
            ctx_text = _text(args, "context")
            job = self.store.add({
                "id": new_id("staff"), "kind": "local", "status": "running",
                "specialist": spec["id"], "title": _text(args, "title", 120) or task[:80],
                "task": task, "created_at": now_iso(),
                "conversation_id": str(kwargs.get("conversation_id") or ""),
            })
            user = task if not ctx_text else f"{task}\n\nContext:\n{ctx_text}"
            try:
                res = self.llm.complete(
                    messages=[{"role": "system", "content": specialists.system_prompt(spec, roster)},
                              {"role": "user", "content": user}],
                    max_tokens=self._int("max_output_tokens", 1200),
                    purpose=f"grokbot-staff.{spec['id']}",
                )
                output = str(getattr(res, "text", "") or "")
            except Exception as e:  # provider errors must not escape
                self.store.update(job["id"], status="failed", error=f"{type(e).__name__}: {e}"[:500])
                return _j({"ok": False, "job_id": job["id"], "error": f"LLM call failed: {type(e).__name__}"})
            self.store.update(job["id"], status="done", output=output)
            return _j({"ok": True, "job_id": job["id"], "specialist": spec["id"],
                       "title": job["title"], "output": output})
        except Exception as e:
            logger.exception("staff_assign failed")
            return _j({"ok": False, "error": f"internal error: {type(e).__name__}"})

    def staff_status(self, args: Dict[str, Any], **kwargs) -> str:
        try:
            jid = _text(args, "job_id", 64)
            if jid:
                job = self.store.get(jid)
                return _j({"ok": bool(job), "job": job} if job else {"ok": False, "error": "job not found"})
            limit = self._int_arg(args, "limit", 10)
            jobs = [{k: j.get(k) for k in ("id", "specialist", "title", "status", "created_at")}
                    for j in self.store.list(kind="local", limit=limit)]
            return _j({"ok": True,
                       "team": [{"id": s["id"], "title": s["title"], "focus": s["focus"]} for s in self.roster()],
                       "jobs": jobs, "bridge": self._bridge_state()})
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
                "\nUsage: /staff <specialist> <task> | /staff status [job_id]"
        if raw.startswith("status"):
            return self.staff_status({"job_id": raw[6:].strip()})
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
