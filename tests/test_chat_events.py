"""Tests for the chat event log (scripts/chat_events.py) and the chat server (scripts/chat_view.py).

Run from the repo root:  python -m unittest discover -s tests -v
"""
import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

os.environ["MULTI_REVIEW_NO_CHAT"] = "1"  # creating a session must not open the chat window in tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chat_events as ce  # noqa: E402
import chat_view as cv  # noqa: E402
import multi_loop as m  # noqa: E402


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


AGENT_FILE = """# claude-opus.md
## Eksiklikler
### CL-007 [Kritik] Review file name can collide with an instruction file
- Yer: `scripts/multi_loop.py:45`
- Durum: KABUL (A, CO)
### ~~CL-008~~ [Dusuk] Wrong line
## Incelemeler
### a.md incelemesi
- A-002: KISMEN (Dusuk) - lines right, no open handle
- A-003: RED - CI already exists: `git log -- .github`
- **A-004 (installer):** KABUL (TAM ONAY).
- CL-007: KABUL - my own id in a review line is not a verdict
## Bana gelen itirazlara cevap
- CL-002 KISMEN (CO): KABUL. README uses `python install.py`.
- ~~CL-004~~ (geri cekildi: duplicate of CL-002)
## Tur gunlugu
- Tur 1 (10:00): A-009 RED mentioned in the log is not a verdict
"""


class Parser(unittest.TestCase):
    def setUp(self):
        self.items = ce.parse_agent_file(AGENT_FILE, "CL")

    def test_findings_with_severity_and_where(self):
        f = self.items["finding:CL-007"]
        self.assertEqual((f["severity"], f["where"]), ("Kritik", "`scripts/multi_loop.py:45`"))
        self.assertIn("finding:CL-008", self.items)

    def test_withdrawals_in_heading_and_body(self):
        self.assertIn("withdraw:CL-008", self.items)
        self.assertEqual(self.items["withdraw:CL-004"]["text"], "geri cekildi: duplicate of CL-002")

    def test_verdict_formats(self):
        self.assertEqual(self.items["verdict:A-002:KISMEN"]["note"], "Dusuk")
        self.assertTrue(self.items["verdict:A-003:RED"]["text"].startswith("CI already exists"))
        self.assertEqual(self.items["verdict:A-004:KABUL"]["note"], "TAM ONAY")
        self.assertEqual(self.items["verdict:A-003:RED"]["target"], "a.md")

    def test_ignores_own_ids_in_reviews_and_lines_outside_sections(self):
        self.assertFalse(any(k.startswith("verdict:CL-") for k in self.items))
        self.assertFalse(any("A-009" in k for k in self.items))
        self.assertFalse(any(k.startswith("verdict:") and "Durum" in v["text"] for k, v in self.items.items()))

    def test_answer_splits_objection_and_reply(self):
        a = self.items["answer:CL-002:KABUL"]
        self.assertEqual((a["objection"], a["by"]), ("KISMEN", "CO"))
        self.assertTrue(a["text"].startswith("README uses"))


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = str(self.tmp / "proj")
        Path(self.root).mkdir()
        self._registry = m.REGISTRY
        m.REGISTRY = self.tmp / "registry" / "active.json"
        quiet(m.do_join, self.root, "claude", "5m", "1h", file="claude-opus.md", prefix="CL", order_mode="random")
        quiet(m.do_join, self.root, "antigravity")

    def tearDown(self):
        m.REGISTRY = self._registry
        shutil.rmtree(self.tmp, ignore_errors=True)

    def events(self):
        return ce.read_events(m.state_dir(self.root))

    def write(self, name, text):
        (m.review_dir(self.root) / name).write_text(text, encoding="utf-8")

    def test_join_logs_session_agents_and_ring(self):
        kinds = [e["kind"] for e in self.events()]
        self.assertEqual(kinds[0], "session")
        self.assertEqual([e["agent"] for e in self.events() if e["kind"] == "join"], ["claude", "antigravity"])
        self.assertIn("ring", kinds)

    def test_sync_is_idempotent_and_orders_a_burst(self):
        self.write("claude-opus.md", AGENT_FILE)
        m.log_events(self.root)
        count = len(self.events())
        m.log_events(self.root)
        self.assertEqual(len(self.events()), count)
        kinds = [e["kind"] for e in self.events() if e.get("agent") == "claude" and e["kind"] != "join"]
        self.assertLess(kinds.index("finding"), kinds.index("verdict"))
        self.assertLess(kinds.index("verdict"), kinds.index("withdraw"))

    def test_changed_verdict_is_a_new_event(self):
        self.write("claude-opus.md", "## Incelemeler\n- A-003: KISMEN - maybe\n")
        m.log_events(self.root)
        self.write("claude-opus.md", "## Incelemeler\n- A-003: RED - no evidence\n")
        m.log_events(self.root)
        self.assertEqual([e["verdict"] for e in self.events() if e["kind"] == "verdict"], ["KISMEN", "RED"])

    def test_leave_and_finalize_keep_the_log(self):
        quiet(m.cmd_leave, argparse.Namespace(root=self.root, agent="antigravity"))
        self.assertIn("leave", [e["kind"] for e in self.events()])
        self.write("claude-opus.md", AGENT_FILE)
        self.write("final.md", "# final\n## Duzeltilecekler\n### F-001 [Kritik] Name collision\n")
        quiet(m.cmd_finalize, argparse.Namespace(root=self.root, agent="claude", force=True, archive=False,
                                                 wait="1s"))
        events = self.events()
        self.assertIn("finding", [e["kind"] for e in events])  # captured before the md files were deleted
        final = [e for e in events if e["kind"] == "final"]
        self.assertEqual(final[0]["items"][0]["id"], "F-001")
        self.assertFalse((m.review_dir(self.root) / "claude-opus.md").exists())

    def test_new_session_archives_the_old_log(self):
        quiet(m.cmd_stop, argparse.Namespace(root=self.root))
        quiet(m.do_join, self.root, "codex", "5m", "1h", order_mode="random")
        self.assertEqual([e.get("agent") for e in self.events() if e["kind"] == "join"], ["codex"])
        self.assertTrue(list((m.state_dir(self.root) / "archive").glob("*/events.jsonl")))

    def test_broken_log_never_breaks_the_loop(self):
        original = ce.sync
        ce.sync = lambda *a, **k: 1 / 0
        try:
            m.log_events(self.root)  # must not raise
        finally:
            ce.sync = original

    def test_busy_lock_skips(self):
        (m.state_dir(self.root) / ce.LOCK_FILE).write_text("")
        self.assertEqual(ce.sync(m.state_dir(self.root), m.review_dir(self.root), m.load_session(self.root)), 0)

    def serve(self):
        server = cv.make_server(m.ChatHub(), port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, f"http://127.0.0.1:{server.server_address[1]}", m.session_key(self.root, "main")

    def get(self, url, **headers):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            e.close()
            return e.code, ""

    def test_hub_serves_page_sessions_and_state(self):
        self.write("claude-opus.md", AGENT_FILE)
        server, base, key = self.serve()
        with urllib.request.urlopen(base + "/") as r:
            page = r.read().decode("utf-8")
            self.assertIn("default-src 'none'", r.headers["Content-Security-Policy"])
        self.assertIn("not a transcript", page)
        sessions = json.loads(self.get(base + "/api/sessions")[1])["sessions"]
        self.assertEqual([(x["key"], x["active"], x["agents"]) for x in sessions], [(key, True, 2)])
        q = urllib.parse.quote(key)
        state = json.loads(self.get(f"{base}/api/state?s={q}&since=0")[1])
        self.assertIn("verdict", [e["kind"] for e in state["events"]])
        self.assertEqual(state["next"], len(state["events"]))
        self.assertEqual({a["name"] for a in state["agents"]}, {"claude", "antigravity"})
        self.assertEqual(state["session"]["project"], "proj")
        self.assertEqual(json.loads(self.get(f"{base}/api/state?s={q}&since={state['next']}")[1])["events"], [])

    def test_hub_serves_only_listed_sessions(self):
        other = self.tmp / "other"  # a real session folder, but never registered: must stay unreadable
        other.mkdir()
        _, base, _ = self.serve()
        for key in (f"{other}|main", f"{self.root}|nope", "..|main", ""):
            self.assertEqual(self.get(f"{base}/api/state?s={urllib.parse.quote(key)}")[0], 404, key)

    def test_hub_rejects_foreign_host(self):  # DNS rebinding: evil.example -> 127.0.0.1
        _, base, key = self.serve()
        for path in ("/", "/api/sessions", f"/api/state?s={urllib.parse.quote(key)}"):
            self.assertEqual(self.get(base + path, Host="evil.example")[0], 403, path)

    def test_named_sessions_and_real_end_time(self):
        m.set_session("docs")
        try:
            quiet(m.do_join, self.root, "codex", "5m", "1h", order_mode="random")
            quiet(m.cmd_stop, argparse.Namespace(root=self.root))
        finally:
            m.set_session("main")
        listed = {x["session"]: x for x in m.ChatHub().sessions()}
        self.assertEqual(set(listed), {"main", "docs"})
        self.assertFalse(listed["docs"]["active"])
        self.assertLessEqual(listed["docs"]["end"], time.time())  # stopped early: not the planned end

    def test_finished_and_new_sessions_are_listed(self):
        quiet(m.cmd_stop, argparse.Namespace(root=self.root))
        second = self.tmp / "second"
        second.mkdir()
        m.set_session("main")
        quiet(m.do_join, str(second), "codex", "5m", "1h", order_mode="random")
        listed = m.ChatHub().sessions()
        self.assertEqual([(Path(x["root"]).name, x["active"]) for x in listed], [("second", True), ("proj", False)])


class UserMessages(Sync):
    """The chat page's message box: stored, addressed, delivered by `wait`, answered in the agent file."""

    def test_addressing(self):
        self.assertEqual(m.post_user_message(self.root, "check the installer")["to"], "all")
        msg = m.post_user_message(self.root, "@antigravity: run the tests")
        self.assertEqual((msg["id"], msg["to"], msg["text"]), ("U-002", "antigravity", "run the tests"))
        self.assertEqual(m.post_user_message(self.root, "@nobody hi")["to"], "all")
        for bad in ("   ", "x" * (m.USER_MAX_CHARS + 1)):
            with self.assertRaises(ValueError):
                m.post_user_message(self.root, bad)

    def test_wait_returns_early_and_only_once(self):
        m.post_user_message(self.root, "@claude only for claude")
        m.post_user_message(self.root, "for everyone")
        kind, line = m.wait_for_tick(self.root, "antigravity", max_wait=60)
        self.assertEqual(kind, "USER")
        self.assertIn("U-002 to=all: for everyone", line)
        self.assertNotIn("U-001", line)
        self.assertIn("## Kullaniciya cevap", line)
        self.assertEqual(m.take_user_messages(self.root, "antigravity"), [])  # shown once
        self.assertEqual([x["id"] for x in m.take_user_messages(self.root, "claude")], ["U-001", "U-002"])

    def test_late_joiner_sees_earlier_messages(self):
        m.post_user_message(self.root, "focus on security")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            m.do_join(self.root, "codex")
        self.assertIn("U-001 to=all: focus on security", out.getvalue())

    def test_stopped_session_takes_no_messages(self):
        quiet(m.cmd_stop, argparse.Namespace(root=self.root))
        with self.assertRaises(ValueError):
            m.post_user_message(self.root, "hello?")

    def test_message_and_reply_become_chat_events(self):
        m.post_user_message(self.root, "is the lock safe?")
        self.write("claude-opus.md", "## Kullaniciya cevap\n- U-001: yes, `os.open` + O_EXCL (`multi_loop.py:300`)\n")
        m.log_events(self.root)
        user = [e for e in self.events() if e["kind"] == "user"]
        reply = [e for e in self.events() if e["kind"] == "reply"]
        self.assertEqual((user[0]["id"], user[0]["text"]), ("U-001", "is the lock safe?"))
        self.assertEqual((reply[0]["agent"], reply[0]["id"]), ("claude", "U-001"))
        self.assertTrue(reply[0]["text"].startswith("yes"))

    def test_reply_formats(self):  # A-001
        for line in ("- U-001: done", "- U-001 (KABUL): done", "- **U-001 (claude):** done", "* U-001 - done"):
            self.assertEqual(ce.parse_agent_file(f"## Kullaniciya cevap\n{line}\n", "CL")["reply:U-001"]["text"],
                             "done", line)

    def test_broken_line_hides_nothing_and_ids_stay_unique(self):  # CL-003
        m.post_user_message(self.root, "first")
        with open(m.state_dir(self.root) / ce.USER_FILE, "a", encoding="utf-8") as fh:
            fh.write('{"id": "U-0\n')  # a write cut in half
        m.post_user_message(self.root, "second")
        self.assertEqual([x["id"] for x in m.user_messages(self.root)], ["U-001", "U-002"])

    def test_unanswered_messages_come_back(self):  # CL-002
        m.post_user_message(self.root, "one")
        m.post_user_message(self.root, "two")
        m.take_user_messages(self.root, "claude")  # shown once, then the agent lost its context
        self.write("claude-opus.md", "## Kullaniciya cevap\n- U-001: answered\n")
        session = m.load_session(self.root)
        self.assertEqual([x["id"] for x in m.unanswered(self.root, session, "claude")], ["U-002"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            m.do_join(self.root, "claude", resume=True)
        self.assertIn("U-002 to=all: two", out.getvalue())
        self.assertNotIn("U-001 to=all", out.getvalue())

    def test_template_has_the_reply_section(self):
        text = (m.review_dir(self.root) / "claude-opus.md").read_text(encoding="utf-8")
        self.assertIn("## Kullaniciya cevap", text)

    def post(self, server, body, **headers):
        port = server.server_address[1]
        h = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
        h.update(headers)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/say", data=json.dumps(body).encode(),
                                     headers={k: v for k, v in h.items() if v is not None}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            e.close()
            return e.code

    def test_say_endpoint_needs_this_page(self):
        server, base, key = self.serve()
        with urllib.request.urlopen(base + "/") as r:
            page = r.read().decode("utf-8")
        token = page.split('TOKEN = "')[1].split('"')[0]
        msg = {"s": key, "text": "x"}
        self.assertEqual(self.post(server, msg), 403)                                     # no token
        self.assertEqual(self.post(server, msg, **{"X-MR-Token": "guess"}), 403)          # wrong token
        self.assertEqual(self.post(server, msg, Origin="http://evil.example", **{"X-MR-Token": token}), 403)
        self.assertEqual(self.post(server, msg, **{"X-MR-Token": token, "Content-Type": "text/plain"}), 403)
        self.assertEqual(self.post(server, {"s": "nope|main", "text": "x"}, **{"X-MR-Token": token}), 404)
        self.assertEqual(m.user_messages(self.root), [])
        self.assertEqual(self.post(server, {"s": key, "text": "  "}, **{"X-MR-Token": token}), 400)
        self.assertEqual(self.post(server, {"s": key, "text": "@claude look at install.py"},
                                   **{"X-MR-Token": token}), 200)
        self.assertEqual(m.user_messages(self.root)[0]["to"], "claude")
        self.assertIn("user", [e["kind"] for e in self.events()])

    def test_finished_session_takes_no_messages_from_the_page(self):
        server, base, key = self.serve()
        with urllib.request.urlopen(base + "/") as r:
            token = r.read().decode("utf-8").split('TOKEN = "')[1].split('"')[0]
        quiet(m.cmd_stop, argparse.Namespace(root=self.root))
        self.assertEqual(self.post(server, {"s": key, "text": "hi"}, **{"X-MR-Token": token}), 400)

    def test_join_does_not_open_a_window_in_tests(self):
        self.assertFalse(m.AUTO_CHAT)
        self.assertIsNone(m.hub_running())


class EmptyHub:
    def sessions(self):
        return []


class Window(unittest.TestCase):
    def test_window_closed_needs_a_page_then_grace(self):
        now = 1000.0
        self.assertFalse(cv.window_closed({"clients": 0, "ever": False, "last": 0}, now))  # never opened yet
        self.assertFalse(cv.window_closed({"clients": 1, "ever": True, "last": 0}, now))   # still open
        self.assertFalse(cv.window_closed({"clients": 0, "ever": True, "last": now - 2}, now))  # reload gap
        self.assertTrue(cv.window_closed({"clients": 0, "ever": True, "last": now - cv.WINDOW_GRACE - 1}, now))

    def test_busy_port_moves_to_the_next(self):
        first = cv.make_server(EmptyHub(), port=0)
        busy = first.server_address[1]
        try:
            second = cv.bind(EmptyHub(), port=busy)
            self.assertNotEqual(second.server_address[1], busy)
            second.server_close()
        finally:
            first.server_close()

    def test_alive_stream_counts_the_page(self):
        server = cv.make_server(EmptyHub(), port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/alive", timeout=5)
            self.assertEqual(r.headers["Content-Type"], "text/event-stream")
            self.assertEqual(r.read(9), b": alive\n\n")
            self.assertEqual(server.alive["clients"], 1)
            r.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
