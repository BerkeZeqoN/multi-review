"""Regression tests for scripts/multi_loop.py (stdlib unittest, no network).

Run from the repo root:  python -m unittest discover -s tests -v
"""
import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import multi_loop as m  # noqa: E402


def quiet(fn, *args, **kwargs):
    """Run fn with stdout captured; return (result, output)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


class TempProject(unittest.TestCase):
    """Each test gets its own project root and its own machine-wide registry."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "proj"
        self.root.mkdir()
        self._registry = m.REGISTRY
        m.REGISTRY = self.tmp / "registry" / "active.json"

    def tearDown(self):
        m.REGISTRY = self._registry
        shutil.rmtree(self.tmp, ignore_errors=True)

    def create(self, agent="claude", mode="negotiated", **kw):
        return quiet(m.do_join, str(self.root), agent, "5m", "1h", order_mode=mode, **kw)

    def join(self, agent, **kw):
        return quiet(m.do_join, str(self.root), agent, **kw)


class PureFunctions(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(m.parse_duration("90"), 5400)
        self.assertEqual(m.parse_duration("1h30m"), 5400)
        self.assertEqual(m.parse_duration("45dk"), 2700)
        self.assertEqual(m.parse_duration("2sa"), 7200)
        with self.assertRaises(SystemExit):
            m.parse_duration("5x")

    def test_canonical_ring_ignores_rotation(self):
        self.assertEqual(m.canonical(["b", "c", "a"]), m.canonical(["a", "b", "c"]))
        self.assertNotEqual(m.canonical(["a", "c", "b"]), m.canonical(["a", "b", "c"]))

    def test_pick_prefix(self):
        self.assertEqual(m.pick_prefix("codex", set()), "CO")
        self.assertEqual(m.pick_prefix("claude-2", {"C"}), "CL")

    def test_squeeze_keeps_head_and_tail(self):
        text = "HEAD" + "x" * 5000 + "TAIL"
        out = m.squeeze(text, 1000)
        self.assertLessEqual(len(out), 1000)
        self.assertTrue(out.startswith("HEAD"))
        self.assertTrue(out.endswith("TAIL"))

    def test_fit_sections_never_cuts_fixed(self):
        # C-001: identity (head) and task (tail) must survive any budget.
        sections = [("YOUR NAME: x", None), ("p" * 5000, 0), ("o" * 5000, 1), ("TASK: write", None)]
        out = m.fit_sections(sections, 2000)
        self.assertIn("YOUR NAME: x", out)
        self.assertIn("TASK: write", out)
        self.assertLessEqual(len(out), 2000)

    def test_extract_file_markers(self):
        out = f"chat\n{m.BEGIN_MARK}\n# f\n## Eksiklikler\n{m.END_MARK}\nbye"
        self.assertEqual(m.extract_file(out), "# f\n## Eksiklikler")

    def test_extract_file_fence_with_chatter_and_inner_block(self):
        # A-007: chatter around the fence is dropped, inner code blocks are kept.
        out = ("Sure:\n```markdown\n# a.md\n## Eksiklikler\n- Kanit:\n```bash\ngrep x\n```\n"
               "## Tur gunlugu\n- Tur 1\n```\nThanks!")
        body = m.extract_file(out)
        self.assertNotIn("Sure", body)
        self.assertNotIn("Thanks", body)
        self.assertIn("## Tur gunlugu", body)
        self.assertIn("grep x", body)

    def test_extract_file_rejects_garbage(self):
        self.assertIsNone(m.extract_file("I cannot help with that."))

    def test_ref_regex(self):
        found = m.REF_RE.findall("see scripts/a.py:5, Dockerfile:12 and Makefile:3; at 14:51 on port x:80")
        self.assertIn(("scripts/a.py", "5"), found)
        self.assertIn(("Dockerfile", "12"), found)  # A-004
        self.assertIn(("Makefile", "3"), found)
        self.assertFalse(any(line in ("51", "80") for _, line in found))


class References(unittest.TestCase):
    def setUp(self):
        # C-002 (a): the project root itself lives in a folder named like DIR_NAME.
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / m.DIR_NAME
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "scripts" / "a.py").write_text("\n".join(f"line{i}" for i in range(1, 21)) + "\n")
        (self.root / m.DIR_NAME).mkdir()
        (self.root / m.DIR_NAME / "c-review.md").write_text("x\n" * 20)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_root_inside_review_named_folder(self):
        out = m.referenced_snippets(self.root, ["scripts/a.py:5"])
        self.assertIn("line5", out)
        self.assertNotIn("NOT FOUND", out)

    def test_short_reference_resolves_by_suffix(self):
        # C-002 (b): `a.py:5` is not relative to the root but matches exactly one file.
        out = m.referenced_snippets(self.root, ["a.py:5"])
        self.assertIn("line5", out)
        self.assertIn("scripts/a.py", out)

    def test_review_files_are_not_code(self):
        out = m.referenced_snippets(self.root, [f"{m.DIR_NAME}/c-review.md:3"])
        self.assertIn("REVIEW FILE", out)

    def test_ambiguous_and_missing(self):
        (self.root / "lib").mkdir()
        (self.root / "lib" / "a.py").write_text("x\n" * 10)
        self.assertIn("AMBIGUOUS", m.referenced_snippets(self.root, ["a.py:2"]))
        self.assertIn("NOT FOUND from project root", m.referenced_snippets(self.root, ["nope.py:2"]))


@unittest.skipUnless(shutil.which("git"), "git not installed")
class ProjectTree(unittest.TestCase):
    def test_untracked_files_listed(self):
        # CO-001: an untracked, non-ignored file must appear next to tracked ones.
        tmp = Path(tempfile.mkdtemp())
        try:
            run = lambda *a: subprocess.run(["git", *a], cwd=tmp, capture_output=True, check=True)
            run("init", "-q")
            (tmp / "README.md").write_text("x")
            (tmp / "new_feature.py").write_text("x")
            (tmp / ".gitignore").write_text("ignored.txt\n")
            (tmp / "ignored.txt").write_text("x")
            run("add", "README.md")
            files, _ = m.project_tree(tmp)
            self.assertIn("README.md", files)
            self.assertIn("new_feature.py", files)
            self.assertNotIn("ignored.txt", files)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Joining(TempProject):
    def test_reserved_file_name_rejected(self):
        # C-007: names that tools load as instructions are refused, case-insensitively.
        for name in ("claude.md", "CLAUDE.md", "Agents.md", "gemini.md"):
            with self.assertRaises(SystemExit, msg=name):
                self.create(file=name)

    def test_custom_file_and_prefix(self):
        (_, info), _ = self.create(file="claude-opus.md", prefix="cl")
        self.assertEqual((info["file"], info["prefix"]), ("claude-opus.md", "CL"))
        self.assertTrue((self.root / m.DIR_NAME / "claude-opus.md").exists())

    def test_duplicate_active_name_needs_resume(self):
        # C-004: a second process with the same name is an error unless it resumes.
        self.create()
        with self.assertRaises(SystemExit):
            self.join("claude")
        _, out = self.join("claude", resume=True)
        self.assertIn("RESUMED", out)

    def test_new_session_archives_old_files(self):
        # C-003: stale md files from a stopped session are moved away, not inherited.
        self.create()
        own = self.root / m.DIR_NAME / "c.md"
        own.write_text("# old\n## Eksiklikler\n### C-001 stale\n")
        quiet(m.cmd_stop, type("A", (), {"root": str(self.root)})())
        _, out = self.create()
        self.assertIn("ARCHIVED 1", out)
        self.assertNotIn("stale", own.read_text())
        archived = list((self.root / m.DIR_NAME / m.STATE_DIR / "archive").rglob("c.md"))
        self.assertEqual(len(archived), 1)
        self.assertIn("stale", archived[0].read_text())


class Negotiation(TempProject):
    def three(self):
        self.create()
        self.join("antigravity")
        self.join("codex")
        return m.load_session(str(self.root))

    def test_unanimous_rotation_is_adopted(self):
        s = self.three()
        s["negotiation"]["proposals"] = {"claude": ["claude", "codex", "antigravity"],
                                         "antigravity": ["codex", "antigravity", "claude"],
                                         "codex": ["antigravity", "claude", "codex"]}
        self.assertEqual(m.try_settle(s), "negotiated: unanimous")

    def test_deadline_tie_keeps_provisional(self):
        # A-002: a tie must not favour whoever voted first.
        s = self.three()
        s["agents"]["gemini"] = {"file": "g.md", "prefix": "G", "joined": 0, "mode": "agent", "left": False}
        s["ring"].append("gemini")
        before = list(s["ring"])
        x, y = ["claude", "antigravity", "codex", "gemini"], ["claude", "codex", "antigravity", "gemini"]
        s["negotiation"]["proposals"] = {"claude": x, "antigravity": y, "codex": x, "gemini": y}
        reason = m.try_settle(s, force=True)
        self.assertIn("tie", reason)
        self.assertEqual(s["ring"], before)

    def test_deadline_plurality_wins(self):
        s = self.three()
        x, y = ["claude", "antigravity", "codex"], ["claude", "codex", "antigravity"]
        s["negotiation"]["proposals"] = {"claude": y, "antigravity": x, "codex": x}
        self.assertIn("plurality 2/3", m.try_settle(s, force=True))
        self.assertEqual(m.canonical(s["ring"]), m.canonical(x))

    def test_leave_to_two_closes_negotiation(self):
        # C-005
        self.three()
        quiet(m.cmd_leave, type("A", (), {"root": str(self.root), "agent": "codex"})())
        s = m.load_session(str(self.root))
        self.assertIsNone(s["negotiation"])
        self.assertEqual(m.ring_members(s), ["claude", "antigravity"])

    def test_left_agent_cannot_wait(self):
        # CO-002
        self.three()
        quiet(m.cmd_leave, type("A", (), {"root": str(self.root), "agent": "codex"})())
        with self.assertRaises(SystemExit):
            m.wait_for_tick(str(self.root), "codex", max_wait=1)


class Waiting(TempProject):
    def test_max_wait_returns_continue(self):
        self.create()
        kind, _ = m.wait_for_tick(str(self.root), "claude", max_wait=1)
        self.assertEqual(kind, "CONTINUE_WAIT")

    def test_stop_is_seen_during_max_wait(self):
        # A-001: stop must end a long max-wait within one slice, not after max-wait.
        import threading
        import time
        self.create()
        stopper = threading.Timer(0.5, lambda: quiet(m.cmd_stop, type("A", (), {"root": str(self.root)})()))
        stopper.start()
        began = time.time()
        kind, _ = m.wait_for_tick(str(self.root), "claude", max_wait=120)
        self.assertEqual(kind, "SESSION_OVER")
        self.assertLess(time.time() - began, 15)


class Final(TempProject):
    def args(self, agent, **kw):
        base = {"root": str(self.root), "agent": agent, "wait": "1s", "archive": False, "force": False}
        base.update(kw)
        return type("A", (), base)()

    def setup_over(self):
        self.create()
        self.join("codex")
        self.join("qwen", mode="drive")
        quiet(m.cmd_stop, type("A", (), {"root": str(self.root)})())
        rd = self.root / m.DIR_NAME
        (rd / "notes.md").write_text("scratch")
        return rd

    def write_final(self, rd):
        (rd / m.FINAL_FILE).write_text(f"# final.md\n\n{m.FINAL_HEADING} (oncelik sirasiyla)\n\n### F-001 x\n")

    def test_final_name_is_reserved(self):
        with self.assertRaises(SystemExit):
            self.create(file="Final.md")

    def test_writer_is_earliest_non_drive(self):
        self.setup_over()
        s = m.load_session(str(self.root))
        self.assertEqual(m.final_writer(s), "claude")
        self.assertIn("final_writer=claude", m.over_line(s))

    def test_final_phase_on_last_tick_only(self):
        self.create()
        s = m.load_session(str(self.root))
        self.assertFalse(m.in_final_phase(s, now=s["start"] + 1))
        self.assertTrue(m.in_final_phase(s, now=s["end"] - s["interval"] + 1))
        self.assertFalse(m.in_final_phase(s, now=s["end"] + 1))

    def test_finalize_requires_heading(self):
        rd = self.setup_over()
        (rd / m.FINAL_FILE).write_text("# final.md\nno section\n")
        with self.assertRaises(SystemExit):
            quiet(m.cmd_finalize, self.args("claude", force=True))
        self.assertTrue((rd / "c.md").exists())

    def test_only_writer_and_waits_for_done(self):
        rd = self.setup_over()
        self.write_final(rd)
        with self.assertRaises(SystemExit):
            quiet(m.cmd_finalize, self.args("codex"))  # not the writer
        with self.assertRaises(SystemExit) as cm:
            quiet(m.cmd_finalize, self.args("claude"))  # codex and qwen not done
        self.assertIn("WAITING_FOR", str(cm.exception.code))
        self.assertTrue((rd / "co.md").exists())

    def test_finalize_deletes_everything_but_final(self):
        rd = self.setup_over()
        self.write_final(rd)
        quiet(m.cmd_done, self.args("codex"))
        m.mark_done(str(self.root), "qwen")
        _, out = quiet(m.cmd_finalize, self.args("claude"))
        self.assertIn("FINALIZED", out)
        self.assertEqual(sorted(p.name for p in rd.glob("*.md")), [m.FINAL_FILE])
        self.assertTrue(m.load_session(str(self.root)).get("finalized"))

    def test_finalize_archive_keeps_copies(self):
        rd = self.setup_over()
        self.write_final(rd)
        quiet(m.cmd_finalize, self.args("claude", force=True, archive=True))
        self.assertEqual(sorted(p.name for p in rd.glob("*.md")), [m.FINAL_FILE])
        kept = sorted(p.name for p in (rd / m.STATE_DIR / "archive").rglob("*.md"))
        self.assertEqual(kept, ["c.md", "co.md", "notes.md", "q.md"])

    def test_finalize_refused_while_running(self):
        self.create()
        self.write_final(self.root / m.DIR_NAME)
        with self.assertRaises(SystemExit):
            quiet(m.cmd_finalize, self.args("claude"))


class Prompt(TempProject):
    def test_small_budget_keeps_identity_and_tail_of_other_file(self):
        # C-001 + A-005
        self.create()
        self.join("codex")
        other = self.root / m.DIR_NAME / "co.md"
        other.write_text("# co.md\n## Eksiklikler\n" + "filler\n" * 20000 + "LATEST ANSWER\n")
        s = m.load_session(str(self.root))
        prompt = m.build_prompt(str(self.root), s, "claude", s["agents"]["claude"], "1", "TICK 1", 20000)
        self.assertIn("YOUR NAME: claude", prompt)
        self.assertIn("TASK:", prompt)
        self.assertIn("LATEST ANSWER", prompt)


if __name__ == "__main__":
    unittest.main()
