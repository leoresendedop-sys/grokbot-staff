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


PASS = json.dumps({"pass": True, "failed": [], "expected": "", "observed": "all shown", "scope": "",
                   "dead_ends": []})
APPROVE = json.dumps({"verdict": "approve", "reasons": ["meets the contract"], "required_changes": []})


def FAIL(crit="has a title", dead=None):
    return json.dumps({"pass": False, "failed": [crit], "expected": "a title line",
                       "observed": "no title", "scope": "first line", "dead_ends": dead or []})


class FakeLLM:
    """Scripted host LLM. ``script`` maps a purpose suffix ('contract', 'verify', 'review', or a
    specialist id) to a list of replies (popped in order; the last one repeats)."""

    def __init__(self, fail=False, script=None):
        self.calls, self.fail = [], fail
        self.script = {k: list(v) for k, v in (script or {}).items()}

    def by(self, suffix):
        return [c for c in self.calls if c[1].get("purpose") == "grokbot-staff." + suffix]

    def complete(self, messages, **kw):
        self.calls.append((messages, kw))
        if self.fail:
            raise RuntimeError("provider down")
        purpose = kw.get("purpose", "")
        key = purpose.split(".", 1)[-1]
        queue = self.script.get(key)
        if queue:
            text = queue.pop(0) if len(queue) > 1 else queue[0]
        elif key == "contract":
            text = '["The answer names at least one idea", "It ends with Next steps"]'
        elif key == "verify":
            text = PASS
        elif key == "review":
            text = APPROVE
        else:
            n = len(self.calls)
            text = f"ANSWER from {purpose}. Draft {n}: " + " ".join(f"idea-{n}-{i}" for i in range(8))
        return types.SimpleNamespace(text=text)


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
        self.assertEqual(ids, ["chief", "research", "design", "writing", "product", "engineering"])

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
        system = llm.by("design")[0][0][0]["content"]
        self.assertIn("Designer", system)
        job = json.loads(st.staff_status({"job_id": out["job_id"]}))["job"]
        self.assertEqual(job["status"], "done")

    def test_unknown_specialist(self):
        st, _ = make()
        out = json.loads(st.staff_assign({"specialist": "nope", "task": "t"}))
        self.assertFalse(out["ok"])
        self.assertIn("chief", out["available"])

    def test_llm_failure_never_raises(self):
        st, _ = make(llm=FakeLLM(fail=True))
        out = json.loads(st.staff_assign({"specialist": "research", "task": "t"}))
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
        self.assertTrue(json.loads(st.slash_staff("research what is trending"))["ok"])


class ContractTests(unittest.TestCase):
    harness = sys.modules["grokbot_staff_under_test.harness"]

    def test_parse_full_contract_top_level_wins(self):
        c = self.harness.parse_contract({
            "task": "Write a tagline", "contract": {"task": "old", "deliverable": "one line",
                                                   "constraints": "no emoji\nunder 60 chars"},
            "inputs": ["brand: Pixel Habit"], "done_when": '["under 60 chars", "mentions habits"]',
            "context": "audience: designers"})
        self.assertEqual(c["task"], "Write a tagline")
        self.assertEqual(c["deliverable"], "one line")
        self.assertEqual(c["constraints"], ["no emoji", "under 60 chars"])
        self.assertEqual(c["inputs"], ["brand: Pixel Habit", "audience: designers"])
        self.assertEqual(c["done_when"], ["under 60 chars", "mentions habits"])
        self.assertEqual(c["done_when_source"], "caller")

    def test_bulleted_done_when_and_json_string_contract(self):
        c = self.harness.parse_contract({"contract": json.dumps({"task": "t", "done_when": "- a\n2. b\n"})})
        self.assertEqual(c["task"], "t")
        self.assertEqual(c["done_when"], ["a", "b"])

    def test_caller_done_when_skips_chief_draft(self):
        llm = FakeLLM()
        st, _ = make(llm=llm)
        out = json.loads(st.staff_assign({"specialist": "writing", "task": "t", "done_when": ["x holds"]}))
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["done_when"], ["x holds"])
        self.assertEqual(llm.by("contract"), [])

    def test_progress_file_survives_restart(self):
        st, tmp = make()
        out = json.loads(st.staff_assign({"specialist": "research", "task": "t"}))
        # a brand-new Staff on the same data dir (session restart) still sees the job files
        st2 = tools.Staff(get_config=lambda k, d=None: d, get_secret=lambda n: None,
                          store=store_mod.JobStore(tmp / "data"), llm=FakeLLM())
        job = json.loads(st2.staff_status({"job_id": out["job_id"]}))["job"]
        self.assertEqual(set(job["progress"]), {"goal", "tried", "learned", "next", "never"})
        self.assertEqual(job["progress"]["goal"], "t")
        self.assertTrue((tmp / "data" / "jobs" / out["job_id"] / "progress.json").is_file())


class LegacyTests(unittest.TestCase):
    def test_legacy_task_only_call(self):
        llm = FakeLLM()
        st, _ = make(llm=llm)
        out = json.loads(st.staff_assign({"specialist": "product", "task": "scope an MVP"}))
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "done")
        self.assertIn("grokbot-staff.product", out["output"])
        self.assertEqual(out["done_when_source"], "chief")
        self.assertEqual(len(out["done_when"]), 2)
        self.assertEqual(len(llm.by("contract")), 1)
        job = json.loads(st.staff_status({"job_id": out["job_id"]}))["job"]
        self.assertEqual(job["contract"]["done_when_source"], "chief")

    def test_unparsable_chief_draft_falls_back(self):
        st, _ = make(llm=FakeLLM(script={"contract": ["sure! here you go"]}))
        out = json.loads(st.staff_assign({"specialist": "design", "task": "t"}))
        self.assertEqual(out["done_when_source"], "fallback")
        self.assertTrue(out["done_when"])

    def test_quality_loop_off_is_single_shot(self):
        llm = FakeLLM()
        st, _ = make(settings={"quality_loop": False}, llm=llm)
        out = json.loads(st.staff_assign({"specialist": "design", "task": "t", "context": "c"}))
        self.assertEqual(out["status"], "done")
        self.assertEqual(len(llm.calls), 1)
        self.assertIn("Context:", llm.calls[0][0][1]["content"])


class LoopTests(unittest.TestCase):
    def test_stops_on_pass(self):
        llm = FakeLLM(script={"verify": [FAIL(), PASS]})
        st, _ = make(llm=llm)
        out = json.loads(st.staff_assign({"specialist": "writing", "task": "t", "done_when": ["has a title"]}))
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["attempts"], 2)
        self.assertEqual(len(llm.by("writing")), 2)
        self.assertEqual(len(llm.by("review")), 1)
        # attempt 2 got attempt 1's structured feedback
        second = llm.by("writing")[1][0][1]["content"]
        self.assertIn("Failed criteria: has a title", second)
        self.assertIn("Observed: no title", second)

    def test_stops_on_identical_output(self):
        llm = FakeLLM(script={"writing": ["Same answer.", "  same   ANSWER!! "], "verify": [FAIL()]})
        st, _ = make(llm=llm)
        out = json.loads(st.staff_assign({"specialist": "writing", "task": "t", "done_when": ["has a title"]}))
        self.assertEqual(out["status"], "needs_human")
        self.assertFalse(out["done"])
        self.assertEqual(out["attempts"], 2)
        self.assertEqual(len(llm.by("verify")), 1)  # the repeat is not re-verified
        self.assertEqual(out["history"][-1]["stopped"], "identical_output")

    def test_near_identical_helper(self):
        pl = sys.modules["grokbot_staff_under_test.pipeline"]
        base = "The plan has five steps: research, design, write, build and launch the product. " * 4
        self.assertTrue(pl.near_identical(base, base.replace("five", "5ive")))
        self.assertFalse(pl.near_identical(base, "A completely different deliverable about pricing."))

    def test_exhaustion_marks_needs_human_with_history(self):
        llm = FakeLLM(script={"verify": [FAIL(dead=["listing colors only"])]})
        st, _ = make(settings={"max_attempts": 3}, llm=llm)
        out = json.loads(st.staff_assign({"specialist": "design", "task": "t", "done_when": ["has a title"]}))
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "needs_human")
        self.assertFalse(out["done"])
        self.assertEqual(out["attempts"], 3)
        self.assertEqual(len(out["history"]), 3)
        self.assertEqual(out["history"][0]["failed"], ["has a title"])
        self.assertEqual(llm.by("review"), [])
        job = json.loads(st.staff_status({"job_id": out["job_id"]}))["job"]
        self.assertEqual(job["status"], "needs_human")
        self.assertEqual(job["progress"]["never"], ["listing colors only"])
        listed = json.loads(st.staff_status({}))
        self.assertIn(out["job_id"], listed["needs_human"])
        self.assertEqual(listed["jobs"][0]["attempts"], 3)

    def test_unparsable_verifier_fails_closed(self):
        st, _ = make(settings={"max_attempts": 2}, llm=FakeLLM(script={"verify": ["Looks great, ship it!"]}))
        out = json.loads(st.staff_assign({"specialist": "design", "task": "t"}))
        self.assertEqual(out["status"], "needs_human")

    def test_self_report_is_not_evidence(self):
        pl = sys.modules["grokbot_staff_under_test.pipeline"]
        # pass=true while listing a failed criterion is not a pass
        fb = pl.parse_feedback(json.dumps({"pass": True, "failed": ["has a title"]}), ["has a title"])
        self.assertFalse(fb["pass"])
        self.assertIn("self-report", pl.VERIFIER)
        # the author's own claims never reach the verifier
        llm = FakeLLM(script={"writing": ["Deliverable body\n\n## Author notes\nI verified every criterion."]})
        st, _ = make(llm=llm)
        json.loads(st.staff_assign({"specialist": "writing", "task": "t", "done_when": ["x"]}))
        self.assertNotIn("I verified every criterion", json.dumps(llm.by("verify")[0][0]))

    def test_review_revise_feeds_back_then_needs_human(self):
        revise = json.dumps({"verdict": "revise", "reasons": ["too vague"], "required_changes": ["add numbers"]})
        llm = FakeLLM(script={"review": [revise]})
        st, _ = make(settings={"max_attempts": 2}, llm=llm)
        out = json.loads(st.staff_assign({"specialist": "product", "task": "t", "done_when": ["x"]}))
        self.assertEqual(out["status"], "needs_human")
        self.assertEqual(out["review"]["verdict"], "revise")
        self.assertIn("add numbers", llm.by("product")[1][0][1]["content"])

    def test_resume_needs_human_job(self):
        llm = FakeLLM(script={"verify": [FAIL(), FAIL(), PASS]})
        st, _ = make(settings={"max_attempts": 2}, llm=llm)
        first = json.loads(st.staff_assign({"specialist": "design", "task": "t", "done_when": ["has a title"]}))
        self.assertEqual(first["status"], "needs_human")
        again = json.loads(st.slash_staff("resume " + first["job_id"]))
        self.assertEqual(again["status"], "done")
        self.assertEqual(again["attempts"], 3)


class GraphTests(unittest.TestCase):
    def test_reviewer_isolation(self):
        notes = "SECRET-AUTHOR-REASONING: I skipped the pricing part"
        llm = FakeLLM(script={"engineering": ["First draft without a heading, endpoints only.\n\n## Author notes\n" + notes,
                                              "Final deliverable text.\n\n## Author notes\n" + notes],
                              "verify": [FAIL(), PASS]})
        st, _ = make(llm=llm)
        out = json.loads(st.staff_assign({"specialist": "engineering", "task": "plan the API",
                                          "done_when": ["has a title"]}))
        self.assertEqual(out["status"], "done")
        review_msgs = llm.by("review")[0][0]
        blob = json.dumps(review_msgs)
        self.assertNotIn("SECRET-AUTHOR-REASONING", blob)
        self.assertNotIn("Author notes", blob)
        self.assertNotIn("Engineer", blob)              # no specialist system prompt
        self.assertNotIn("Observed: no title", blob)    # no verifier feedback / history
        self.assertIn("plan the API", blob)             # contract is there
        self.assertIn("Final deliverable text.", blob)  # deliverable is there
        self.assertEqual([m["role"] for m in review_msgs], ["system", "user"])
        # notes are kept in the job files for the human, not delivered
        self.assertNotIn("SECRET-AUTHOR-REASONING", out["output"])
        job = json.loads(st.staff_status({"job_id": out["job_id"]}))["job"]
        self.assertEqual(job["review"]["verdict"], "approve")

    def test_state_flows_through_files(self):
        st, tmp = make()
        out = json.loads(st.staff_assign({"specialist": "research", "task": "t", "done_when": ["x"]}))
        d = tmp / "data" / "jobs" / out["job_id"]
        for f in ("contract.json", "progress.json", "attempt-1.json", "review.json", "deliverable.md"):
            self.assertTrue((d / f).is_file(), f)
        self.assertEqual((d / "deliverable.md").read_text(), out["output"])


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
        out = json.loads(st.grokbot_delegate({"task": "deep research", "specialist": "research"}))
        self.assertTrue(out["ok"])
        req = op.requests[0][0]
        body = json.loads(req.data)
        self.assertEqual(set(body), {"id", "created_at", "specialist", "title", "task", "reply_hint"})
        self.assertEqual(body["specialist"], "research")
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
