"""Tests for the chat event log (scripts/chat_events.py) and the chat server (scripts/chat_view.py).

Run from the repo root:  python -m unittest discover -s tests -v
"""
import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

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

    def test_chat_server_serves_page_and_state(self):
        self.write("claude-opus.md", AGENT_FILE)
        server = cv.make_server(m.state_dir(self.root), lambda: m.describe(self.root),
                                lambda: m.log_events(self.root), port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with urllib.request.urlopen(base + "/") as r:
                page = r.read().decode("utf-8")
                self.assertIn("default-src 'none'", r.headers["Content-Security-Policy"])
            self.assertIn("not a transcript", page)
            with urllib.request.urlopen(base + "/api/state?since=0") as r:
                state = json.loads(r.read().decode("utf-8"))
            self.assertIn("verdict", [e["kind"] for e in state["events"]])
            self.assertEqual(state["next"], len(state["events"]))
            self.assertEqual({a["name"] for a in state["agents"]}, {"claude", "antigravity"})
            with urllib.request.urlopen(base + f"/api/state?since={state['next']}") as r:
                self.assertEqual(json.loads(r.read().decode("utf-8"))["events"], [])
        finally:
            server.shutdown()
            server.server_close()


class Window(unittest.TestCase):
    def test_window_closed_needs_a_page_then_grace(self):
        now = 1000.0
        self.assertFalse(cv.window_closed({"clients": 0, "ever": False, "last": 0}, now))  # never opened yet
        self.assertFalse(cv.window_closed({"clients": 1, "ever": True, "last": 0}, now))   # still open
        self.assertFalse(cv.window_closed({"clients": 0, "ever": True, "last": now - 2}, now))  # reload gap
        self.assertTrue(cv.window_closed({"clients": 0, "ever": True, "last": now - cv.WINDOW_GRACE - 1}, now))

    def test_alive_stream_counts_the_page(self):
        server = cv.make_server(tempfile.mkdtemp(), lambda: {}, lambda: None, port=0)
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
