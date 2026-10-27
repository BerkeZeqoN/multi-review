"""Tests for install.py: only the skill files are copied, updates back up outside skills folders."""
import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import install  # noqa: E402


class Installer(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / ".claude").mkdir()
        self.dest = self.home / ".claude" / "skills" / install.NAME
        self._tools, self._home = install.TOOLS, install.HOME
        install.HOME = self.home
        install.TOOLS = {"claude": (self.home / ".claude", self.dest),
                         "codex": (self.home / ".codex", self.home / ".codex" / "skills" / install.NAME)}

    def tearDown(self):
        install.TOOLS, install.HOME = self._tools, self._home
        shutil.rmtree(self.home, ignore_errors=True)

    def run_main(self, *argv):
        old = sys.argv
        sys.argv = ["install.py", *argv]
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                install.main()
        finally:
            sys.argv = old
        return out.getvalue()

    def test_installs_only_skill_files_for_detected_tools(self):
        self.run_main()
        got = sorted(str(p.relative_to(self.dest)).replace("\\", "/") for p in self.dest.rglob("*") if p.is_file())
        self.assertEqual(got, sorted(install.FILES))
        self.assertFalse((self.home / ".codex").exists())  # codex not installed -> untouched

    def test_claude_gets_the_join_command(self):
        self.run_main()
        cmd = self.home / ".claude" / "commands" / "multi-review-join.md"
        self.assertTrue(cmd.is_file())
        self.run_main("--uninstall")
        self.assertFalse(cmd.exists())

    def test_opencode_gets_slash_commands_and_shares_the_agents_skill(self):
        shared = self.home / ".agents" / "skills" / install.NAME
        (self.home / ".config" / "opencode").mkdir(parents=True)
        install.TOOLS = {"agents": (self.home / ".agents", shared),
                         "opencode": (self.home / ".config" / "opencode", shared)}
        self.run_main()
        cmds = self.home / ".config" / "opencode" / "commands"
        self.assertEqual(sorted(p.name for p in cmds.iterdir()), ["multi-review-join.md", "multi-review.md"])
        self.assertTrue((shared / "SKILL.md").is_file())  # installed although ~/.agents did not exist
        self.assertIn("shared", self.run_main())  # now both are detected: the folder is written once
        self.run_main("--uninstall")
        self.assertFalse(shared.exists())
        self.assertEqual(list(cmds.iterdir()), [])

    def test_update_backs_up_outside_skills(self):
        self.run_main()
        (self.dest / "stale.txt").write_text("old")
        out = self.run_main()
        self.assertIn("update", out)
        self.assertFalse((self.dest / "stale.txt").exists())
        backups = list((self.home / f".{install.NAME}" / "backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])  # nothing extra in skills/

    def test_dry_run_and_uninstall(self):
        self.run_main("--dry-run")
        self.assertFalse(self.dest.exists())
        self.run_main()
        self.run_main("--uninstall")
        self.assertFalse(self.dest.exists())

    def test_unknown_target(self):
        with self.assertRaises(SystemExit):
            self.run_main("--target", "nope")


if __name__ == "__main__":
    unittest.main()
