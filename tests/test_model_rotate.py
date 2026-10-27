import contextlib
import io
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import model_rotate as mr  # noqa: E402

# A fake model CLI: "a" is out of quota, "c" crashes, every other model answers.
FAKE = '''import sys
model, prompt = sys.argv[1], sys.stdin.read()
if model == "a":
    sys.exit(print("Error: rate limit exceeded for free tier", file=sys.stderr) or 1)
if model == "c":
    sys.exit(print("Error: Model unavailable", file=sys.stderr) or 1)
print(f"{model}: {prompt}")
'''


class Rotate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "fake.py").write_text(FAKE, encoding="utf-8")
        self.cmd = f'"{sys.executable}" "{self.tmp / "fake.py"}" {{model}}'
        self._state = mr.STATE_DIR
        mr.STATE_DIR = self.tmp / "state"

    def tearDown(self):
        mr.STATE_DIR = self._state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, models, prompt="hi", *extra):
        old_argv, old_in = sys.argv, sys.stdin
        sys.argv = ["model_rotate.py", "--models", models, "--cmd", self.cmd, *extra]
        sys.stdin = io.StringIO(prompt)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = mr.main()
        finally:
            sys.argv, sys.stdin = old_argv, old_in
        return code, out.getvalue(), err.getvalue()

    def state(self, models):
        return mr.load_state(mr.state_path(self.cmd, models.split(",")))

    def test_used_up_model_is_skipped_and_reported(self):
        code, out, err = self.call("a,b")
        self.assertEqual((code, out.strip()), (0, "b: hi"))
        self.assertIn("MODEL_SWITCH - -> b (a quota:", err)
        st = self.state("a,b")
        self.assertEqual(st["current"], "b")
        self.assertGreater(st["cooldown"]["a"], time.time() + 3000)  # quota: the long cooldown

    def test_next_call_starts_with_the_last_good_model(self):
        self.call("a,b")
        code, out, err = self.call("a,b", "again")
        self.assertEqual((code, out.strip(), err), (0, "b: again", ""))  # a is not tried again, no switch

    def test_other_errors_get_the_short_cooldown(self):
        self.call("c,b")
        left = self.state("c,b")["cooldown"]["c"] - time.time()
        self.assertTrue(0 < left <= 300)

    def test_all_failed(self):
        code, out, err = self.call("a,c")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("MODEL_ROTATE_FAILED", err)

    def test_cooled_down_models_are_tried_last_not_never(self):
        self.call("a,b")
        st = mr.load_state(mr.state_path(self.cmd, ["a", "b"]))
        self.assertEqual(mr.order(["a", "b"], st, time.time()), ["b", "a"])

    def test_bad_input(self):
        with self.assertRaises(SystemExit):
            mr.parse_models("ok,bad model;rm")
        with self.assertRaises(SystemExit):
            self.call("a", "hi", "--cmd", "opencode run")  # no {model}


if __name__ == "__main__":
    unittest.main()
