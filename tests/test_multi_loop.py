"""Regression tests for scripts/multi_loop.py (stdlib unittest, no network).

Run from the repo root:  python -m unittest discover -s tests -v
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["MULTI_REVIEW_NO_CHAT"] = "1"  # creating a session must not open the chat window in tests
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
        self._registry, self._fallback = m.REGISTRY, m.FALLBACK_DIR
        m.REGISTRY = self.tmp / "registry" / "active.json"
        m.FALLBACK_DIR = self.tmp / "fallback"

    def tearDown(self):
        m.REGISTRY, m.FALLBACK_DIR = self._registry, self._fallback
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

    def test_goal_is_saved_and_shown(self):
        _, out = self.create(goal="  repo quality  ")
        self.assertEqual(m.load_session(str(self.root))["goal"], "repo quality")
        self.assertIn("GOAL=repo quality", out)
        _, out = self.join("codex")
        self.assertIn("GOAL=repo quality", out)
        # C-017: creator-only options on an active session are an error, not silently dropped.
        with self.assertRaises(SystemExit) as cm:
            self.join("gemini", goal="security")
        self.assertIn("SESSION_EXISTS", str(cm.exception.code))

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


class WorkModes(TempProject):
    def write_findings(self, name, n):
        info = m.load_session(str(self.root))["agents"][name]
        body = "".join(f"### {info['prefix']}-{i:03d} [Orta] x\n" for i in range(1, n + 1))
        (self.root / m.DIR_NAME / info["file"]).write_text("# f\n## Eksiklikler\n" + body)

    def test_default_is_research_for_everyone(self):
        _, out = self.create()
        self.join("codex")
        s = m.load_session(str(self.root))
        self.assertEqual(s["work_mode"], "research")
        self.assertIn("WORK_MODE=research YOUR_TASK=research+review", out)
        self.assertTrue(m.must_research(s, "codex"))

    def test_idle_researcher_gets_warning(self):
        # The reported bug: a late joiner that only comments must be flagged.
        self.create()
        self.join("codex")
        s = m.load_session(str(self.root))
        self.write_findings("codex", 1)
        self.assertNotIn("WORK_WARNING", m.work_check(str(self.root), s, "codex"))  # baseline
        self.assertNotIn("WORK_WARNING", m.work_check(str(self.root), s, "codex"))  # idle 1
        self.assertIn("WORK_WARNING", m.work_check(str(self.root), s, "codex"))     # idle 2
        self.write_findings("codex", 2)
        line = m.work_check(str(self.root), s, "codex")
        self.assertIn("own_findings=2 new=1", line)
        self.assertNotIn("WORK_WARNING", line)

    def test_logged_clean_scan_clears_idle(self):
        # A clean area is valid research when the log says what was checked (PROTOCOL "Every TICK" 4).
        self.create()
        s = m.load_session(str(self.root))
        own = self.root / m.DIR_NAME / "c.md"
        m.work_check(str(self.root), s, "claude")
        m.work_check(str(self.root), s, "claude")
        self.assertIn("WORK_WARNING", m.work_check(str(self.root), s, "claude"))
        own.write_text(own.read_text() + "- Tur 3 (10:00): Taze tarama: src/auth/*, grep -n token -> temiz\n")
        line = m.work_check(str(self.root), s, "claude")
        self.assertIn("clean_scans=1", line)
        self.assertNotIn("WORK_WARNING", line)

    def test_round_log_ignores_quoted_heading(self):
        # The heading quoted in a sentence before the real section must not hide the real log.
        text = ("## Eksiklikler\n- only lines under `## Tur gunlugu` count\n\n## Tur gunlugu\n"
                "- Tur 1 (10:00): Taze tarama: src -> temiz\n")
        self.assertEqual(len(m.SCAN_RE.findall(m.round_log(text))), 1)

    def test_quoted_scan_outside_round_log_does_not_count(self):
        # CO-003: quoting the protocol example in a review is not research.
        self.create()
        own = self.root / m.DIR_NAME / "c.md"
        text = own.read_text().replace(
            "## Bana gelen itirazlara cevap",
            "- A-002: KISMEN - ornek `Taze tarama: src/auth/*` sadece aniliyor\n"
            "- Tur 9 (10:00): Taze tarama: x -> temiz (alinti, log degil)\n\n## Bana gelen itirazlara cevap")
        own.write_text(text)
        self.assertEqual(m.count_findings(str(self.root), m.load_session(str(self.root))["agents"]["claude"]),
                         (0, 0))

    def test_review_mode_only_lead_must_research(self):
        self.create(work_mode="review")
        self.join("codex")
        s = m.load_session(str(self.root))
        self.assertTrue(m.must_research(s, "claude"))
        self.assertFalse(m.must_research(s, "codex"))
        self.assertIn("verify-only (lead=claude)", m.your_task(s, "codex"))
        for _ in range(3):
            line = m.work_check(str(self.root), s, "codex")
        self.assertNotIn("WORK_WARNING", line)

    def test_split_mode_covers_every_area(self):
        _, out = self.create(work_mode="split", areas="api, ui ,db,docs,tests")
        self.join("codex")
        self.join("gemini")
        s = m.load_session(str(self.root))
        self.assertIn("AREAS=api,ui,db,docs,tests", out)
        got = [a for n in m.ring_members(s) for a in m.agent_areas(s, n)]
        self.assertEqual(sorted(got), sorted(s["areas"]))
        self.assertTrue(all(m.agent_areas(s, n) for n in m.ring_members(s)))

    def test_split_mode_default_areas_from_tree(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "b.md").write_text("x")
        (self.root / "setup.cfg").write_text("x")
        self.create(work_mode="split")
        self.assertEqual(m.load_session(str(self.root))["areas"], ["docs/", "src/", "(root files)"])


class MultiSession(TempProject):
    """Several reviews at once: named sessions, in one project or across projects."""

    def tearDown(self):
        m.set_session(m.DEFAULT_SESSION)
        super().tearDown()

    def in_session(self, name, fn, *a, **kw):
        m.set_session(name)
        return fn(*a, **kw)

    def test_two_sessions_in_one_project_are_isolated(self):
        self.in_session("main", self.create)
        self.in_session("security", self.create, goal="security")
        rd = self.root / m.DIR_NAME
        self.assertTrue((rd / "c.md").exists())
        self.assertTrue((rd / "security" / "c.md").exists())
        m.set_session("main")
        self.assertIsNone(m.load_session(str(self.root)).get("goal"))
        m.set_session("security")
        self.assertEqual(m.load_session(str(self.root))["goal"], "security")
        m.set_session("main")
        self.assertEqual(sorted(p.name for p in rd.glob("*.md")), ["c.md"])  # main never sees security's files

    def test_join_without_session_is_ambiguous_then_named(self):
        self.in_session("main", self.create)
        self.in_session("docs", self.create, goal="docs")
        import os
        old = os.getcwd()
        os.chdir(self.root)
        try:
            with self.assertRaises(SystemExit) as cm:
                quiet(m.resolve_root, None)
            self.assertIn("MULTIPLE_SESSIONS", str(cm.exception.code))
            self.assertIn("--session docs", str(cm.exception.code))
            self.assertIn('goal="docs"', str(cm.exception.code))
            self.assertEqual(m.resolve_root(None, "docs"), (str(self.root.resolve()), "docs"))
        finally:
            os.chdir(old)

    def test_session_name_found_across_projects(self):
        other = self.tmp / "other"
        other.mkdir()
        m.set_session("api")
        quiet(m.do_join, str(other), "claude", "5m", "1h", order_mode="random")
        root, sess = quiet(m.resolve_root, None, "api")[0]
        self.assertEqual((root, sess), (str(other.resolve()), "api"))

    def test_next_line_carries_session(self):
        _, out = self.in_session("security", self.create)
        self.assertIn("SESSION=security DIR=multi-review/security", out)
        self.assertRegex(out, r"NEXT: python .* --session security wait --agent claude")

    def test_status_prints_next_with_session(self):
        # CO-006: read-only commands also hand over the selected --root/--session.
        self.in_session("security", self.create)
        _, out = quiet(m.cmd_status, type("A", (), {"root": str(self.root)})())
        self.assertRegex(out, r"NEXT: python .* --session security join --agent <you>")

    def test_bad_session_name(self):
        for bad in ("Has Space", "../x", "state", "x" * 40):
            with self.assertRaises(SystemExit, msg=bad):
                m.set_session(bad)


class Liveness(TempProject):
    def test_agent_without_wait_becomes_stale(self):
        # An agent whose loop died must show up, not silently block finalize.
        import time
        self.create()
        self.join("codex")
        s = m.load_session(str(self.root))
        now = time.time()
        m.touch_seen(str(self.root), "claude")
        self.assertFalse(m.is_stale(str(self.root), s, "claude", now))
        self.assertFalse(m.is_stale(str(self.root), s, "codex", now))  # just joined
        later = now + 3 * s["interval"] + 1
        self.assertTrue(m.is_stale(str(self.root), s, "codex", later))
        # CO-005: a drive agent whose driver died is listed too.
        s["agents"]["codex"]["mode"] = "drive"
        import unittest.mock as mock
        with mock.patch.object(m.time, "time", return_value=later):
            self.assertIn("codex", m.stale_agents(str(self.root), s))
        _, out = quiet(m.print_roster, str(self.root), s, "claude")
        self.assertIn("seen=", out)

    def test_wait_refreshes_heartbeat(self):
        self.create()
        m.wait_for_tick(str(self.root), "claude", max_wait=1)
        seen = m.load_json(m.state_dir(str(self.root)) / "agent_claude.seen.json")
        self.assertIsNotNone(seen)


class Sandbox(TempProject):
    def test_denied_write_is_not_reported_as_busy_lock(self):
        # A Codex sandbox refused every write and join waited 15 s for "lock busy".
        import time
        import unittest.mock as mock
        self.create()
        real_open = m.os.open

        def deny(path, *a, **kw):
            if str(path).endswith(".lock"):
                raise PermissionError(13, "Access is denied", str(path))
            return real_open(path, *a, **kw)

        start = time.time()
        with mock.patch.object(m.os, "open", deny), mock.patch.object(m, "dir_writable", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                self.join("codex")
        self.assertIn("permission denied", str(cm.exception))
        self.assertIn("Codex", str(cm.exception))
        self.assertLess(time.time() - start, 5)

    def test_delete_pending_lock_still_retries(self):
        # Windows "delete pending": only the lock name is refused; the folder is writable, so wait.
        import unittest.mock as mock
        self.create()
        real_open = m.os.open
        calls = {"n": 0}

        def pending_once(path, *a, **kw):
            if str(path).endswith(".lock") and calls["n"] == 0:
                calls["n"] += 1
                raise PermissionError(13, "Access is denied", str(path))
            return real_open(path, *a, **kw)

        with mock.patch.object(m.os, "open", pending_once):
            self.join("codex")
        self.assertIn("codex", m.active_agents(m.load_session(str(self.root))))

    def test_dir_writable(self):
        self.assertTrue(m.dir_writable(self.root))
        self.assertEqual(list(self.root.glob(".write-probe-*")), [])

    def home_denied(self, *denied):
        # Antigravity's sandbox refused ~/.multi-review: the creator crashed (or, patched, skipped the
        # registry) and agents started in other folders never found the session.
        import unittest.mock as mock
        real = m.dir_writable
        return mock.patch.object(m, "dir_writable", lambda d: Path(d) not in denied and real(d))

    def test_registry_falls_back_when_home_is_not_writable(self):
        other = self.tmp / "other"
        other.mkdir()
        m.set_session("api")
        try:
            with self.home_denied(m.REGISTRY.parent):
                _, out = quiet(m.do_join, str(other), "antigravity", "5m", "1h", order_mode="random")
        finally:
            m.set_session(m.DEFAULT_SESSION)
        self.assertIn("REGISTRY=", out)
        self.assertFalse(m.REGISTRY.exists())
        root, sess = quiet(m.resolve_root, None, "api")[0]  # another agent, other folder, home writable
        self.assertEqual((root, sess), (str(other.resolve()), "api"))

    def test_no_writable_registry_prints_the_join_command(self):
        with self.home_denied(m.REGISTRY.parent, m.FALLBACK_DIR):
            _, out = self.create(agent="antigravity")
        self.assertIn("REGISTRY_UNAVAILABLE", out)
        self.assertIn(f'--root "{self.root.resolve()}" join --agent NAME', out)
        self.assertIn("antigravity", m.active_agents(m.load_session(str(self.root))))  # session itself works

    def test_joiner_registers_what_a_sandboxed_creator_could_not(self):
        with self.home_denied(m.REGISTRY.parent, m.FALLBACK_DIR):
            self.create(agent="antigravity")
        self.assertFalse(m.registered(str(self.root)))
        self.join("claude")  # home writable: it fills in the registry and the hub's session list
        self.assertTrue(m.registered(str(self.root)))
        self.assertTrue(m.chat_hub.remembered(str(self.root)))

    def test_sandboxed_joiner_writes_nothing_machine_wide(self):
        self.create()
        m.REGISTRY.unlink()
        with self.home_denied(m.REGISTRY.parent, m.FALLBACK_DIR):
            _, out = self.join("antigravity")
        self.assertNotIn("REGISTRY_UNAVAILABLE", out)  # only the creator is told how others join
        self.assertFalse(m.REGISTRY.exists())

    def test_hub_opened_by_joiner_when_creator_could_not(self):
        import unittest.mock as mock
        calls = []
        with mock.patch.object(m.chat_hub, "open_chat_hub", lambda root: calls.append(root) and False):
            self.create(agent="antigravity")
        self.assertFalse(m.load_session(str(self.root)).get("chat_opened"))
        with mock.patch.object(m.chat_hub, "open_chat_hub", lambda root: calls.append(root) or True):
            self.join("claude")
            self.join("codex")  # hub was opened once; a panel the user closed is not reopened
        self.assertEqual(len(calls), 2)
        self.assertTrue(m.load_session(str(self.root))["chat_opened"])

    def test_antigravity_outside_root_is_told_to_open_the_folder(self):
        self.create()
        _, out = self.join("antigravity")  # tests run outside the temp project
        self.assertIn("WORKSPACE_OUTSIDE_ROOT", out)
        self.assertIn("open-ide antigravity", out)
        _, out = self.join("gemini")
        self.assertNotIn("WORKSPACE_OUTSIDE_ROOT", out)

    def test_open_ide_starts_the_cli_on_root(self):
        import unittest.mock as mock
        started = []
        with mock.patch.object(m.ide_open, "find_cli", lambda ide: "agy"), \
                mock.patch.object(m.ide_open.subprocess, "Popen", lambda args, **kw: started.append(args)):
            _, out = self.create(open_ide="antigravity")
        self.assertEqual(started, [["agy", str(self.root.resolve())]])
        self.assertIn("IDE_OPENED antigravity", out)
        self.assertNotIn("WORKSPACE_OUTSIDE_ROOT", out)

    def test_open_ide_without_cli(self):
        import unittest.mock as mock
        with mock.patch.object(m.ide_open, "find_cli", lambda ide: None):
            ok, out = quiet(m.ide_open.open_ide, str(self.root), "antigravity")
        self.assertFalse(ok)
        self.assertIn("IDE_NOT_FOUND", out)

    def test_prune_cleans_both_registries(self):
        with self.home_denied(m.REGISTRY.parent):
            self.create()
        quiet(m.cmd_stop, __import__("argparse").Namespace(root=str(self.root)))
        self.assertEqual(m.load_json(m.FALLBACK_DIR / "active.json"), [])


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

    def test_drive_only_session_has_a_writer_who_finalizes(self):
        # CO-004: with no terminal agent the earliest drive agent writes final.md and finalizes.
        self.create("qwen")
        s = m.load_session(str(self.root))
        s["agents"]["qwen"]["mode"] = "drive"
        m.save_json(m.session_path(str(self.root)), s)
        self.join("llama", mode="drive")
        s = m.load_session(str(self.root))
        self.assertEqual(m.final_writer(s), "qwen")
        self.assertEqual(m.work_lead(s), "qwen")
        quiet(m.cmd_stop, type("A", (), {"root": str(self.root)})())
        m.mark_done(str(self.root), "llama")
        fake = self.tmp / "fake_model.py"
        fake.write_text("import sys\nsys.stdin.read()\n"
                        f"print('chat\\n{m.BEGIN_MARK}\\n# final.md\\n\\n{m.FINAL_HEADING}\\n\\n### F-001 x\\n"
                        f"{m.END_MARK}')\n")
        args = type("A", (), {"max_chars": 50000, "exec": f'"{sys.executable}" "{fake}"', "call_timeout": "1m",
                              "agent": "qwen"})()
        quiet(m.drive_finalize, str(self.root), args)
        rd = self.root / m.DIR_NAME
        self.assertEqual(sorted(p.name for p in rd.glob("*.md")), [m.FINAL_FILE])
        self.assertIn("F-001", (rd / m.FINAL_FILE).read_text())
        self.assertTrue(list((rd / m.STATE_DIR / "archive").rglob("q.md")))  # archived, not deleted

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

    def test_done_waits_for_final_draft(self):
        # Early `stop`: no final-phase tick, so others must not finish before the draft exists.
        rd = self.setup_over()
        with self.assertRaises(SystemExit) as cm:
            quiet(m.cmd_done, self.args("codex"))
        self.assertIn("WAITING_FOR_FINAL", str(cm.exception.code))
        quiet(m.cmd_done, self.args("codex", force=True))
        self.write_final(rd)
        _, out = quiet(m.cmd_done, self.args("codex"))
        self.assertIn("DONE codex", out)

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
