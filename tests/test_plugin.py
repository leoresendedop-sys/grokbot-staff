"""Stdlib unit tests: no Hermes, no network."""
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_pkg():
    name = "grokbot_staff_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pkg = _load_pkg()
tools = sys.modules["grokbot_staff_under_test.tools"]
store_mod = sys.modules["grokbot_staff_under_test.store"]
bridge = sys.modules["grokbot_staff_under_test.bridge"]
specialists = sys.modules["grokbot_staff_under_test.specialists"]
delegation = sys.modules["grokbot_staff_under_test.delegation"]


class FakeLLM:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def complete(self, messages, **kw):
        self.calls.append((messages, kw))
        if self.fail:
            raise RuntimeError("provider down")
        return types.SimpleNamespace(text="ANSWER from " + kw.get("purpose", ""))


class FakeResp:
    def __init__(self, status=200):
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, status=200, exc=None):
        self.status, self.exc, self.requests = status, exc, []

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        if self.exc:
            raise self.exc
        return FakeResp(self.status)


def make(settings=None, secret=None, llm=None, opener=None):
    tmp = tempfile.mkdtemp()
    settings = settings or {}
    st = tools.Staff(get_config=lambda k, d=None: settings.get(k, d),
                     get_secret=lambda n: secret, store=store_mod.JobStore(Path(tmp) / "data"),
                     llm=llm if llm is not None else FakeLLM(), opener=opener)
    return st, Path(tmp)


class RosterTests(unittest.TestCase):
    def test_defaults(self):
        ids = [s["id"] for s in specialists.load_specialists([])]
        self.assertEqual(ids, ["chief", "saas", "design", "vision-agents", "computer-vision", "x"])

    def test_custom_json_and_invalid_dropped(self):
        raw = json.dumps([{"id": "legal", "title": "Legal", "prompt": "p"}, {"id": "BAD ID"}, "x"])
        r = specialists.load_specialists(raw)
        self.assertEqual([s["id"] for s in r], ["legal"])

    def test_garbage_falls_back(self):
        self.assertEqual(len(specialists.load_specialists("{not json")), 6)


class StaffTests(unittest.TestCase):
    def test_assign_routes_prompt_and_persists(self):
        llm = FakeLLM()
        st, _ = make(llm=llm)
        out = json.loads(st.staff_assign({"specialist": "design", "task": "hero ideas"}))
        self.assertTrue(out["ok"])
        self.assertIn("grokbot-staff.design", out["output"])
        system = llm.calls[0][0][0]["content"]
        self.assertIn("Design researcher", system)
        job = json.loads(st.staff_status({"job_id": out["job_id"]}))["job"]
        self.assertEqual(job["status"], "done")

    def test_unknown_specialist(self):
        st, _ = make()
        out = json.loads(st.staff_assign({"specialist": "nope", "task": "t"}))
        self.assertFalse(out["ok"])
        self.assertIn("chief", out["available"])

    def test_llm_failure_never_raises(self):
        st, _ = make(llm=FakeLLM(fail=True))
        out = json.loads(st.staff_assign({"specialist": "saas", "task": "t"}))
        self.assertFalse(out["ok"])
        self.assertEqual(json.loads(st.staff_status({"job_id": out["job_id"]}))["job"]["status"], "failed")

    def test_bad_args_never_raise(self):
        st, _ = make()
        for fn in (st.staff_assign, st.staff_status, st.grokbot_delegate, st.grokbot_collect, st.grokbot_status):
            json.loads(fn({"task": None, "limit": "abc", "job_id": 5}))

    def test_status_lists_team(self):
        st, _ = make()
        out = json.loads(st.staff_status({}))
        self.assertEqual(len(out["team"]), 6)

    def test_slash_staff(self):
        st, _ = make()
        self.assertIn("chief", st.slash_staff(""))
        self.assertTrue(json.loads(st.slash_staff("x what is trending"))["ok"])


class BridgeTests(unittest.TestCase):
    def test_fail_closed_without_config(self):
        st, _ = make()
        out = json.loads(st.grokbot_delegate({"task": "research"}))
        self.assertEqual(out["error"], "not_configured")
        self.assertIn("webhook", out["how_to_fix"].lower())
        self.assertEqual(json.loads(st.grokbot_status({}))["jobs"], [])

    def test_webhook_post_and_redaction(self):
        op = FakeOpener()
        url = "https://hooks.example.com/routine/SECRET-TOKEN?k=1"
        st, _ = make(secret=url, opener=op)
        out = json.loads(st.grokbot_delegate({"task": "deep research", "specialist": "saas"}))
        self.assertTrue(out["ok"])
        req = op.requests[0][0]
        body = json.loads(req.data)
        self.assertEqual(set(body), {"id", "created_at", "specialist", "title", "task", "reply_hint"})
        self.assertEqual(body["specialist"], "saas")
        dumped = json.dumps(out) + st.grokbot_status({})
        self.assertNotIn("SECRET-TOKEN", dumped)

    def test_webhook_rejects_http_and_userinfo(self):
        self.assertIsNotNone(bridge.validate_webhook_url("http://example.com/x"))
        self.assertIsNotNone(bridge.validate_webhook_url("https://u:p@example.com/x"))
        self.assertIsNone(bridge.validate_webhook_url("http://127.0.0.1:8080/x"))

    def test_redirect_refused(self):
        err = urllib.error.HTTPError("https://a/", 302, "Found", {}, io.BytesIO())
        st, _ = make(secret="https://a.example/x", opener=FakeOpener(exc=err))
        out = json.loads(st.grokbot_delegate({"task": "t"}))
        self.assertFalse(out["ok"])
        self.assertIn("redirect", out["delivery"]["webhook"]["detail"])

    def test_no_redirect_handler(self):
        h = bridge._NoRedirect()
        self.assertIsNone(h.redirect_request(None, None, 302, "", {}, "https://evil/"))

    def test_timeout_reported(self):
        st, _ = make(secret="https://a.example/x", opener=FakeOpener(exc=urllib.error.URLError("timed out")))
        out = json.loads(st.grokbot_delegate({"task": "t"}))
        self.assertEqual(out["status"], "failed")

    def test_inbox_roundtrip(self):
        st, tmp = make()
        inbox = tmp / "inbox"
        st.get_config = lambda k, d=None: str(inbox) if k == "grokbot_inbox_dir" else d
        out = json.loads(st.grokbot_delegate({"task": "t"}))
        jid = out["job_id"]
        self.assertTrue((inbox / "handoffs" / f"{jid}.json").exists())
        self.assertFalse(json.loads(st.grokbot_collect({"job_id": jid}))["ok"])
        (inbox / "results" / f"{jid}.json").write_text(json.dumps({"result": "done!"}))
        res = json.loads(st.grokbot_collect({"job_id": jid}))
        self.assertEqual(res["result"], "done!")
        self.assertEqual(json.loads(st.grokbot_status({"status": "done"}))["jobs"][0]["id"], jid)

    def test_pasted_result(self):
        st, tmp = make(settings={"grokbot_inbox_dir": str(tempfile.mkdtemp())})
        jid = json.loads(st.grokbot_delegate({"task": "t"}))["job_id"]
        res = json.loads(st.grokbot_collect({"job_id": jid, "result": "pasted"}))
        self.assertEqual(res["source"], "pasted")

    def test_path_traversal_id(self):
        self.assertIsNone(bridge.read_inbox_result("/tmp", "../etc/passwd"))


class DelegationTests(unittest.TestCase):
    def test_routes(self):
        roster = specialists.load_specialists([{"id": "a"}, {"id": "b", "profile": "work"}])
        p = delegation.make_provider(lambda: roster)
        self.assertEqual(p.task_parameters({})["specialist"]["enum"], ["a", "b"])
        self.assertEqual(p.route_task({"specialist": "a"}, {"model": "m"})["label"], "a")
        self.assertEqual(p.route_task({"specialist": "b"}, {}), {"profile": "work", "label": "b"})
        with self.assertRaises(Exception):
            p.route_task({"specialist": "zzz"}, {})


class RegisterTests(unittest.TestCase):
    def test_register_matches_manifest(self):
        reg = {"tools": [], "commands": [], "skills": []}

        class Ctx:
            llm = FakeLLM()

            def get_config(self, k, default=None):
                return default

            def register_tool(self, name, **kw):
                reg["tools"].append(name)

            def register_command(self, name, handler, **kw):
                reg["commands"].append(name)

            def register_skill(self, name, path):
                reg["skills"].append(name)

        pkg.register(Ctx())
        manifest = (ROOT / "plugin.yaml").read_text()
        for t in reg["tools"]:
            self.assertIn(f"  - {t}\n", manifest)
        self.assertEqual(len(reg["tools"]), 5)
        self.assertEqual(reg["commands"], ["staff", "grokbot"])
        self.assertEqual(reg["skills"], ["grokbot-staff"])


if __name__ == "__main__":
    unittest.main()
