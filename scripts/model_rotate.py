#!/usr/bin/env python3
"""Run one prompt (stdin) through a list of models, moving on when one is used up.

Meant as the `drive --exec` command, so a review keeps going on free models whose quota runs out
mid-session:

  python multi_loop.py drive --agent opencode-free --exec "python model_rotate.py --models free"

Each call starts with the model that answered last. A model that fails (non-zero exit, empty answer,
timeout) is skipped for a cooldown: long when the error looks like a quota or rate limit, short for
anything else. Every switch is printed to stderr as `MODEL_SWITCH old -> new (reason)`; the driver
shows those lines. State is kept per command in the temp folder, so a restarted driver remembers it.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_CMD = "opencode run -m {model}"
MODEL_RE = re.compile(r"^[\w.:/@-]{1,120}$")
QUOTA_RE = re.compile(r"rate.?limit|too many requests|\b429\b|\b402\b|quota|exhaust|usage limit|limit (reached|"
                      r"exceeded)|insufficient|credit|free (tier|usage|limit)|capacity|overloaded", re.I)
STATE_DIR = Path(tempfile.gettempdir()) / "multi-review" / "model-rotate"
UNITS = {"s": 1, "m": 60, "h": 3600}


def seconds(text):
    m = re.fullmatch(r"(\d+)([smh]?)", text.strip().lower())
    if not m:
        sys.exit(f"ERROR: bad duration '{text}' (use e.g. 90s, 10m, 2h)")
    return int(m.group(1)) * UNITS[m.group(2) or "m"]


def free_models():
    """Every model `opencode models` lists as free (opencode/*-free), in its order."""
    try:
        out = subprocess.run("opencode models", shell=True, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    found = [line.strip() for line in out.splitlines() if line.strip().endswith("-free")]
    if not found:
        sys.exit("ERROR: `opencode models` listed no free model; pass --models a,b,c")
    return found


def parse_models(text):
    if text.strip().lower() == "free":
        return free_models()
    models = [m.strip() for m in text.split(",") if m.strip()]
    bad = [m for m in models if not MODEL_RE.match(m)]
    if not models or bad:
        sys.exit(f"ERROR: --models must be a comma-separated list of model ids (bad: {bad})")
    return models


def state_path(cmd, models):
    key = hashlib.sha1((cmd + "|" + ",".join(models)).encode("utf-8")).hexdigest()[:12]
    return STATE_DIR / f"{key}.json"


def load_state(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"current": None, "cooldown": {}}


def save_state(path, state):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # rotation still works for this call; it only forgets between calls


def order(models, state, now):
    """Last good model first, then the rest in list order; cooled-down ones only if nothing else is left."""
    cur = state.get("current")
    start = models.index(cur) if cur in models else 0
    ring = models[start:] + models[:start]
    ready = [m for m in ring if state["cooldown"].get(m, 0) <= now]
    waiting = sorted((m for m in ring if m not in ready), key=lambda m: state["cooldown"][m])
    return ready + waiting


def reason_of(code, out, err):
    err = re.sub(r"\x1b\[[0-9;]*m", "", err)  # opencode colors its errors
    last = (err.strip().splitlines() or [""])[-1][:160]
    if code == "timeout":
        return "timeout", False
    if QUOTA_RE.search(err) or QUOTA_RE.search(out[-2000:]):
        return f"quota: {last}", True
    if code != 0:
        return f"exit {code}: {last}", False
    return "empty answer", False


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", default="free", help='comma-separated model ids, or "free" (default): every '
                                                     "opencode/*-free model `opencode models` lists")
    p.add_argument("--cmd", default=DEFAULT_CMD, help=f"command per model, {{model}} is replaced (default: "
                                                       f"{DEFAULT_CMD!r}); it reads the prompt on stdin")
    p.add_argument("--model-timeout", default="8m", help="give up on one model after this long (default 8m)")
    p.add_argument("--quota-cooldown", default="1h", help="skip a used-up model this long (default 1h)")
    p.add_argument("--error-cooldown", default="5m", help="skip a model that failed otherwise (default 5m)")
    args = p.parse_args()
    if "{model}" not in args.cmd:
        sys.exit("ERROR: --cmd must contain {model}")
    models = parse_models(args.models)
    prompt = sys.stdin.read()
    path = state_path(args.cmd, models)
    state = load_state(path)
    state.setdefault("cooldown", {})
    tried = []
    for model in order(models, state, time.time()):
        try:
            res = subprocess.run(args.cmd.replace("{model}", model), shell=True, input=prompt, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=seconds(args.model_timeout))
            code, out, err = res.returncode, res.stdout or "", res.stderr or ""
        except subprocess.TimeoutExpired:
            code, out, err = "timeout", "", ""
        if code == 0 and out.strip():
            if state.get("current") != model:
                print(f"MODEL_SWITCH {state.get('current') or '-'} -> {model} "
                      f"({'; '.join(tried) or 'first call'})", file=sys.stderr)
            state["current"] = model
            state["cooldown"].pop(model, None)
            save_state(path, state)
            sys.stdout.write(out)
            return 0
        why, quota = reason_of(code, out, err)
        state["cooldown"][model] = time.time() + seconds(args.quota_cooldown if quota else args.error_cooldown)
        tried.append(f"{model} {why}")
    save_state(path, state)
    print("MODEL_ROTATE_FAILED: every model failed: " + " | ".join(tried), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
