#!/usr/bin/env python3
"""Shared clock + review ring for the multi-agent mutual review loop.

Any number of agents (Claude Code, Antigravity, Codex, Gemini CLI, Cursor,
a plain LLM driven by `drive`, ...) review one project. Each agent owns one
markdown file under <root>/multi-review/ (c.md, a.md, co.md, ...). Agents sit
in a ring: each one reviews the NEXT agent's file and answers the PREVIOUS
agent (its reviewer). When an agent joins or leaves, the ring is rebuilt:
  order_mode=random      script reshuffles the ring with a random permutation
  order_mode=negotiated  newcomer is appended provisionally, then agents agree
                         on an order with `propose`; no agreement after two
                         ticks -> most-voted proposal wins
State lives in <root>/multi-review/.state/.

Commands (run from the project root, or pass --root):
  join    --agent NAME [--interval 5m --duration 2h --order-mode random|negotiated]
          create a session (needs interval, duration, order mode) or join the active one
  wait    --agent NAME [--max-wait 9m]     block until the next tick
  propose --agent NAME --order a,b,c       negotiated mode: vote for a ring order
  shuffle                                  random mode: reshuffle the ring now
  roster | status                          agents, files, ring, clock
  leave   --agent NAME                     leave the session (ring closes the gap)
  stop                                     end the session for everyone
  drive   --agent NAME --exec "CMD" [...]  run the whole loop for a text-only
          model: each round pipes a prompt into CMD (stdin) and writes the
          returned markdown into the agent's own file
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

DIR_NAME = "multi-review"
STATE_DIR = ".state"
# Machine-wide list of active sessions, so `join` from any folder (any tool) finds the project.
REGISTRY = Path.home() / ".multi-review" / "active.json"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
FILE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,60}\.md$")
BEGIN_MARK = "===BEGIN FILE==="
END_MARK = "===END FILE==="
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build",
             ".next", ".nuxt", "out", "target", ".idea", ".vscode", "coverage", DIR_NAME,
             ".duo", ".cache", "vendor"}
ORDER_MODES = ("random", "negotiated")
NEGOTIATE_TICKS = 2
# Stable prefixes so the usual tools get the familiar files (c.md, a.md, co.md, ...).
KNOWN_PREFIX = {"claude": "C", "antigravity": "A", "codex": "CO", "gemini": "G", "cursor": "CU",
                "copilot": "CP", "opencode": "OC", "qwen": "Q"}
KEY_FILES = ["README.md", "README", "package.json", "pyproject.toml", "requirements.txt",
             "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "composer.json", "Dockerfile"]

UNITS = {
    "s": 1, "sn": 1, "sec": 1,
    "m": 60, "dk": 60, "min": 60,
    "h": 3600, "sa": 3600, "saat": 3600, "hr": 3600,
}


def parse_duration(text):
    """Parse '90', '5m', '1h30m', '2sa', '45dk'. Bare numbers are minutes."""
    text = text.strip().lower().replace(" ", "")
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return int(float(text) * 60)
    parts = re.findall(r"(\d+(?:\.\d+)?)([a-z]+)", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        sys.exit(f"ERROR: cannot parse duration '{text}' (use e.g. 5m, 2h, 1h30m, 45dk)")
    total = 0
    for num, unit in parts:
        if unit not in UNITS:
            sys.exit(f"ERROR: unknown unit '{unit}' in '{text}'")
        total += float(num) * UNITS[unit]
    return int(total)


def fmt(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def stamp(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def review_dir(root):
    d = Path(root).resolve() / DIR_NAME
    (d / STATE_DIR).mkdir(parents=True, exist_ok=True)
    return d


def state_dir(root):
    return review_dir(root) / STATE_DIR


def session_path(root):
    return state_dir(root) / "session.json"


def load_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_session(root):
    return load_json(session_path(root))


def is_active(session, now=None):
    now = now or time.time()
    return bool(session) and not session.get("stopped") and session["end"] > now


def peek_session(root):
    """Read a session without creating any folder (used while resolving the root)."""
    try:
        return load_json(Path(root).resolve() / DIR_NAME / STATE_DIR / "session.json")
    except (OSError, ValueError):
        return None


def registry_active():
    """Active sessions from the machine-wide registry; dead entries are dropped."""
    try:
        entries = load_json(REGISTRY, [])
    except (OSError, ValueError):
        entries = []
    return [e for e in entries if is_active(peek_session(e["root"]))]


def registry_add(root):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    root = str(Path(root).resolve())
    entries = [e for e in registry_active() if e["root"].lower() != root.lower()]
    entries.append({"root": root, "created": stamp(time.time())})
    save_json(REGISTRY, entries)


def registry_prune():
    if REGISTRY.exists():
        save_json(REGISTRY, registry_active())


def resolve_root(arg, creating=False):
    """--root wins; else an active session in cwd; else the single active session in the registry."""
    if arg:
        return str(Path(arg).resolve())
    cwd = Path.cwd()
    if is_active(peek_session(cwd)):
        return str(cwd)
    active = registry_active()
    if len(active) == 1:
        print(f"ROOT={active[0]['root']} (found in {REGISTRY})")
        return active[0]["root"]
    if len(active) > 1:
        roots = "\n".join(f"  --root \"{e['root']}\"" for e in active)
        sys.exit(f"MULTIPLE_SESSIONS: more than one review is active; pick one:\n{roots}")
    if creating:
        sys.exit("NO_ACTIVE_SESSION: pass --root <project folder> to create one")
    return str(cwd)


def active_agents(session):
    return {n: a for n, a in session.get("agents", {}).items() if not a.get("left")}


class Lock:
    """Cross-process lock so two agents joining at once don't clobber session.json."""

    def __init__(self, root):
        self.path = state_dir(root) / "session.lock"

    def __enter__(self):
        deadline = time.time() + 15
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 30:
                        self.path.unlink()  # stale lock from a crashed process
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    sys.exit(f"ERROR: lock busy: {self.path}")
                time.sleep(0.2)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def pick_prefix(name, used):
    known = KNOWN_PREFIX.get(re.sub(r"-\d+$", "", name))
    base = re.sub(r"[^a-z0-9]", "", name).upper() or "X"
    for cand in ([known] if known else []) + [base[:1], base[:2], base[:3]]:
        if cand not in used:
            return cand
    i = 2
    while f"{base[:1]}{i}" in used:
        i += 1
    return f"{base[:1]}{i}"


def template(name, info):
    return (
        f"# {info['file']} - {name} review notes\n\n"
        f"> Written only by `{name}`. Finding ids: {info['prefix']}-001, {info['prefix']}-002, ...\n"
        f"> Joined: {stamp(info['joined'])}. Other agents answer in their own files.\n\n"
        "## Eksiklikler\n\n_(henuz yok)_\n\n"
        "## Incelemeler\n\n_(halkada inceledigin her dosya icin bir `### <dosya> incelemesi` alt basligi)_\n\n"
        "## Bana gelen itirazlara cevap\n\n_(henuz yok)_\n\n"
        "## Tur gunlugu\n\n"
    )


def rel(root, path):
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def print_clock(session):
    now = time.time()
    tick = int((now - session["start"]) // session["interval"])
    print(f"interval={fmt(session['interval'])} start={stamp(session['start'])} "
          f"end={stamp(session['end'])} tick={tick} remaining={fmt(session['end'] - now)}"
          f"{' STOPPED' if session.get('stopped') else ''}")


def current_tick(session):
    return int((time.time() - session["start"]) // session["interval"])


def ring_members(session):
    active = active_agents(session)
    return [n for n in session.get("ring", []) if n in active]


def ring_neighbors(session, agent):
    """Return (review target, reviewer): agent reviews the next one, answers the previous one."""
    ring = ring_members(session)
    if agent not in ring or len(ring) < 2:
        return None, None
    i = ring.index(agent)
    return ring[(i + 1) % len(ring)], ring[i - 1]


def canonical(order):
    """Rings equal up to rotation compare equal."""
    order = list(order)
    if not order:
        return ()
    i = order.index(min(order))
    return tuple(order[i:] + order[:i])


def ring_text(session):
    ring = ring_members(session)
    if not ring:
        return "-"
    files = [session["agents"][n]["file"] for n in ring]
    return " > ".join(files + [files[0]]) if len(files) > 1 else files[0]


def set_ring(session, order, reason):
    session["ring"] = list(order)
    session["ring_version"] = session.get("ring_version", 0) + 1
    session.setdefault("ring_log", []).append(
        {"version": session["ring_version"], "at": stamp(time.time()), "reason": reason,
         "ring": ring_text(session)})


def shuffle_ring(names, previous):
    """Random permutation; for 3+ agents avoid repeating the previous cycle."""
    rng = random.SystemRandom()
    names = list(names)
    prev = canonical([n for n in previous if n in names])
    for _ in range(50):
        rng.shuffle(names)
        if len(names) < 3 or canonical(names) != prev or len(prev) != len(names):
            break
    return names


def rebuild_ring_on_join(session, newcomer):
    active = list(active_agents(session))
    current = [n for n in session.get("ring", []) if n in active]
    if session["order_mode"] == "random":
        set_ring(session, shuffle_ring(active, current), f"random shuffle ({newcomer} joined)")
        return
    # Negotiated: newcomer goes to the end provisionally so it can work right away.
    set_ring(session, current + [n for n in active if n not in current], f"provisional ({newcomer} joined)")
    if len(active) >= 3:
        session["negotiation"] = {"since": time.time(), "since_tick": current_tick(session),
                                  "proposals": {}, "trigger": f"{newcomer} joined"}


def voters(session):
    """Text-only drive agents cannot negotiate, so they abstain."""
    return [n for n, a in active_agents(session).items() if a.get("mode") != "drive"]


def valid_proposals(session):
    neg = session.get("negotiation") or {}
    members = sorted(active_agents(session))
    return {n: p for n, p in neg.get("proposals", {}).items()
            if n in voters(session) and sorted(p) == members}


def try_settle(session, force=False):
    """Adopt a negotiated order on unanimity, or (force) on plurality after the deadline."""
    if not session.get("negotiation"):
        return None
    props = valid_proposals(session)
    shapes = Counter(canonical(p) for p in props.values())
    if props and len(props) == len(voters(session)) and len(shapes) == 1:
        set_ring(session, next(iter(props.values())), "negotiated: unanimous")
    elif force and props:
        best, _ = shapes.most_common(1)[0]
        winner = next(p for p in props.values() if canonical(p) == best)
        set_ring(session, winner, f"negotiated: deadline, plurality {shapes[best]}/{len(voters(session))}")
    elif force:
        session.setdefault("ring_log", []).append(
            {"version": session.get("ring_version", 0), "at": stamp(time.time()),
             "reason": "negotiated: deadline, no proposals, provisional order kept", "ring": ring_text(session)})
    else:
        return None
    session["negotiation"] = None
    return session["ring_log"][-1]["reason"]


def negotiation_text(session):
    neg = session.get("negotiation")
    if not neg:
        return "order_status=SETTLED"
    props = valid_proposals(session)
    shown = ";".join(f"{n}:{'>'.join(p)}" for n, p in props.items()) or "-"
    waiting = ",".join(n for n in voters(session) if n not in props) or "-"
    deadline = neg["since_tick"] + NEGOTIATE_TICKS
    return f"order_status=NEGOTIATING proposals={shown} waiting_for={waiting} deadline_tick={deadline}"


def print_ring(session, me=None):
    print(f"RING v{session.get('ring_version', 0)} mode={session.get('order_mode')}: {ring_text(session)}")
    if session.get("order_mode") == "negotiated":
        print(negotiation_text(session))
    if me:
        target, reviewer = ring_neighbors(session, me)
        f = lambda n: session["agents"][n]["file"] if n else "-"
        print(f"YOUR_ROLE: review={f(target)} answer={f(reviewer)}")


def print_roster(root, session, me=None):
    print_ring(session, me)
    print("ROSTER:")
    for name, a in session.get("agents", {}).items():
        flags = []
        if a.get("left"):
            flags.append("LEFT")
        if name == me:
            flags.append("YOU")
        print(f"  - {name:<14} prefix={a['prefix']:<4} file={DIR_NAME}/{a['file']:<20} "
              f"mode={a.get('mode', 'agent'):<6} joined={stamp(a['joined'])}"
              f"{' [' + ','.join(flags) + ']' if flags else ''}")


def do_join(root, agent, interval=None, duration=None, file=None, prefix=None, mode="agent", order_mode=None):
    if not NAME_RE.match(agent):
        sys.exit("ERROR: --agent must be lowercase [a-z0-9-], 1-24 chars (e.g. claude, codex, gemini-2)")
    if file and not FILE_RE.match(file):
        sys.exit("ERROR: --file must be a plain name ending in .md (e.g. c.md)")
    now = time.time()
    with Lock(root):
        session = load_session(root)
        if not is_active(session, now):
            if not (interval and duration and order_mode):
                sys.exit("NO_ACTIVE_SESSION: to create one pass --interval, --duration and "
                         "--order-mode random|negotiated (ask the user which order mode)")
            iv, du = parse_duration(interval), parse_duration(duration)
            if iv < 30:
                sys.exit("ERROR: interval must be at least 30s")
            if du < iv:
                sys.exit("ERROR: duration must be >= interval")
            session = {"start": now, "interval": iv, "end": now + du, "root": str(Path(root).resolve()),
                       "stopped": False, "order_mode": order_mode, "agents": {}, "ring": [],
                       "ring_version": 0, "ring_log": [], "negotiation": None}
            for old in state_dir(root).glob("agent_*.json"):
                old.unlink()
            registry_add(root)
            print(f"CREATED new session (order_mode={order_mode})")
        else:
            print(f"JOINED existing session (started {stamp(session['start'])}, "
                  f"order_mode={session['order_mode']}; your --interval/--duration/--order-mode are ignored)")
        agents = session.setdefault("agents", {})
        if agent in agents:
            info = agents[agent]
            if info.get("left"):
                info["left"] = False
                print(f"REJOINED as {agent} (kept file {info['file']}, prefix {info['prefix']})")
                rebuild_ring_on_join(session, agent)
            else:
                print(f"WARNING: '{agent}' is already active. If you are a different agent, "
                      "leave and join with another name (e.g. claude-2).")
        else:
            used_files = {a["file"].lower() for a in agents.values()}
            used_prefixes = {a["prefix"] for a in agents.values()}
            if prefix:
                prefix = prefix.upper()
                if not re.fullmatch(r"[A-Z0-9]{1,4}", prefix) or prefix in used_prefixes:
                    sys.exit(f"ERROR: prefix {prefix} invalid or taken ({sorted(used_prefixes)})")
            else:
                prefix = pick_prefix(agent, used_prefixes)
            file = file or f"{prefix.lower()}.md"
            if file.lower() in used_files:
                sys.exit(f"ERROR: file {file} already belongs to another agent; pass --file")
            info = {"file": file, "prefix": prefix, "joined": now, "mode": mode, "left": False}
            agents[agent] = info
            rebuild_ring_on_join(session, agent)
        save_json(session_path(root), session)
    own = review_dir(root) / info["file"]
    if not own.exists():
        own.write_text(template(agent, info), encoding="utf-8")
        print(f"created {own}")
    # Baseline: the first TICK only reports changes that appear after joining.
    snapshot(root, session, agent)
    print(f"ROOT={Path(root).resolve()}")
    print(f"YOU={agent} prefix={info['prefix']} own_file={own}")
    print_clock(session)
    print_roster(root, session, me=agent)
    return session, info


def cmd_join(args):
    do_join(args.root, args.agent, args.interval, args.duration, args.file, args.prefix,
            order_mode=args.order_mode)


def snapshot(root, session, agent):
    """Role files of this agent and whether they (or the ring) changed since its last tick."""
    state_path = state_dir(root) / f"agent_{agent}.json"
    state = load_json(state_path, {"mtimes": {}, "ring_version": None, "roles": {}})
    target, reviewer = ring_neighbors(session, agent)
    roles, mtimes = {}, dict(state["mtimes"])
    for role, name in (("review", target), ("answer", reviewer)):
        if not name:
            roles[role] = ("-", "no")
            continue
        a = session["agents"][name]
        f = review_dir(root) / a["file"]
        mtime = f.stat().st_mtime if f.exists() else 0
        changed = mtime != state["mtimes"].get(name) or state["roles"].get(role) != name
        roles[role] = (a["file"], "yes" if changed else "no")
        mtimes[name] = mtime
    ring_changed = state.get("ring_version") != session.get("ring_version")
    prev_roles = state.get("roles", {})
    save_json(state_path, {"mtimes": mtimes, "ring_version": session.get("ring_version"),
                           "roles": {"review": target, "answer": reviewer}})
    return roles, ring_changed, prev_roles


def settle_if_due(root):
    """Negotiation deadline: after NEGOTIATE_TICKS ticks the plurality order (or provisional) wins."""
    with Lock(root):
        session = load_session(root)
        neg = session.get("negotiation")
        if neg and current_tick(session) >= neg["since_tick"] + NEGOTIATE_TICKS:
            try_settle(session, force=True)
            save_json(session_path(root), session)


def wait_for_tick(root, agent, max_wait=None):
    """Block until the next tick. Returns (kind, line) with kind in TICK|CONTINUE_WAIT|SESSION_OVER."""
    session = load_session(root)
    if not session:
        sys.exit("ERROR: no session. Run join first.")
    if agent not in session.get("agents", {}):
        sys.exit(f"ERROR: '{agent}' has not joined. Run join --agent {agent} first.")
    began = time.time()
    while True:
        session = load_session(root)
        now = time.time()
        if not is_active(session, now):
            return "SESSION_OVER", "SESSION_OVER"
        elapsed = now - session["start"]
        next_tick = session["start"] + (int(elapsed // session["interval"]) + 1) * session["interval"]
        target = min(next_tick, session["end"])
        if max_wait is not None and target - began > max_wait:
            time.sleep(max(0, began + max_wait - now))
            return "CONTINUE_WAIT", "CONTINUE_WAIT (max-wait reached, run wait again)"
        # Sleep in short slices so `stop` is noticed quickly.
        if target - now > 0:
            time.sleep(min(10, target - now))
            if time.time() < target:
                continue
        if not is_active(load_session(root)):
            return "SESSION_OVER", "SESSION_OVER"
        settle_if_due(root)
        session = load_session(root)
        roles, ring_changed, prev_roles = snapshot(root, session, agent)
        line = (f"TICK {current_tick(session)} review={roles['review'][0]} review_changed={roles['review'][1]} "
                f"answer={roles['answer'][0]} answer_changed={roles['answer'][1]} "
                f"ring_changed={'yes' if ring_changed else 'no'}")
        if ring_changed:
            files = {r: session["agents"][n]["file"] for r, n in prev_roles.items()
                     if n and n in session["agents"]}
            line += f" previous_review={files.get('review', '-')} previous_answer={files.get('answer', '-')}"
        return "TICK", line


def cmd_wait(args):
    max_wait = parse_duration(args.max_wait) if args.max_wait else None
    _, line = wait_for_tick(args.root, args.agent, max_wait)
    print(line)
    session = load_session(args.root)
    print_clock(session)
    print_roster(args.root, session, me=args.agent)


def cmd_propose(args):
    with Lock(args.root):
        session = load_session(args.root)
        if not is_active(session):
            sys.exit("NO_ACTIVE_SESSION")
        if session.get("order_mode") != "negotiated":
            sys.exit("ERROR: this session uses order_mode=random; the script owns the order")
        active = active_agents(session)
        if args.agent not in active:
            sys.exit(f"ERROR: '{args.agent}' is not an active agent")
        order = [x.strip() for x in args.order.split(",") if x.strip()]
        if sorted(order) != sorted(active):
            sys.exit(f"ERROR: --order must list every active agent exactly once: {','.join(sorted(active))}")
        if not session.get("negotiation"):
            # Reopening a settled order is allowed; it runs to the same deadline rule.
            session["negotiation"] = {"since": time.time(), "since_tick": current_tick(session),
                                      "proposals": {}, "trigger": f"{args.agent} reopened"}
        session["negotiation"]["proposals"][args.agent] = order
        result = try_settle(session)
        save_json(session_path(args.root), session)
    print(f"PROPOSED {'>'.join(order)}" + (f" -> ADOPTED ({result})" if result else ""))
    print_ring(session, args.agent)


def cmd_shuffle(args):
    with Lock(args.root):
        session = load_session(args.root)
        if not is_active(session):
            sys.exit("NO_ACTIVE_SESSION")
        if session.get("order_mode") != "random":
            sys.exit("ERROR: this session uses order_mode=negotiated; use propose")
        set_ring(session, shuffle_ring(list(active_agents(session)), session.get("ring", [])), "manual shuffle")
        save_json(session_path(args.root), session)
    print_ring(session)


def cmd_roster(args):
    session = load_session(args.root)
    if not session:
        print("NO_SESSION")
        return
    print_roster(args.root, session)


def cmd_status(args):
    session = load_session(args.root)
    if not session:
        print("NO_SESSION")
        return
    print_clock(session)
    print_roster(args.root, session)


def cmd_leave(args):
    with Lock(args.root):
        session = load_session(args.root)
        if not session or args.agent not in session.get("agents", {}):
            print("NOT_IN_SESSION")
            return
        session["agents"][args.agent]["left"] = True
        # Close the gap without reshuffling: the leaver's reviewer now reviews the leaver's target.
        set_ring(session, ring_members(session), f"{args.agent} left")
        save_json(session_path(args.root), session)
    print(f"LEFT {args.agent}")
    print_ring(session)


def cmd_stop(args):
    with Lock(args.root):
        session = load_session(args.root)
        if not session:
            print("NO_SESSION")
            return
        session["stopped"] = True
        save_json(session_path(args.root), session)
    registry_prune()
    print("STOPPED")


# ---------------------------------------------------------------- drive mode

def project_tree(root, limit=400):
    root = Path(root).resolve()
    try:
        out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20)
        files = [f for f in out.stdout.splitlines() if not f.startswith(DIR_NAME + "/")]
        if out.returncode == 0 and files:
            return files[:limit], len(files)
    except (OSError, subprocess.SubprocessError):
        pass
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            files.append(rel(root, Path(dirpath) / fn))
    return files[:limit], len(files)


def head(path, lines=80, max_chars=6000):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[:lines])[:max_chars]


def referenced_snippets(root, texts, radius=8, limit=40):
    """Pull code around every `path:line` reference so a text-only model can verify claims."""
    root = Path(root).resolve()
    refs, seen = [], set()
    for text in texts:
        for path, line in re.findall(r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+):(\d+)", text):
            key = (path, int(line))
            if key not in seen:
                seen.add(key)
                refs.append(key)
    out = []
    for path, line in refs[:limit]:
        target = (root / path).resolve()
        if root not in target.parents or DIR_NAME in target.parts or not target.is_file():
            out.append(f"--- {path}:{line} -> FILE NOT FOUND (possible invented reference)")
            continue
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        if line > len(lines):
            out.append(f"--- {path}:{line} -> LINE OUT OF RANGE (file has {len(lines)} lines)")
            continue
        lo, hi = max(1, line - radius), min(len(lines), line + radius)
        body = "\n".join(f"{n:>5}| {lines[n - 1]}" for n in range(lo, hi + 1))
        out.append(f"--- {path}:{line}\n{body}")
    return "\n\n".join(out)


def build_prompt(root, session, agent, info, round_label, tick_line, max_chars):
    rd = review_dir(root)
    protocol = head(Path(__file__).resolve().parent.parent / "PROTOCOL.md", lines=400, max_chars=20000)
    own_text = (rd / info["file"]).read_text(encoding="utf-8", errors="replace")
    target, reviewer = ring_neighbors(session, agent)
    others, roles = {}, {}
    for role, name in (("REVIEW THIS (your target)", target), ("ANSWER THIS (your reviewer)", reviewer)):
        if name:
            others[name] = session["agents"][name]
            roles.setdefault(name, []).append(role)
    other_texts = {}
    for name, a in others.items():
        f = rd / a["file"]
        other_texts[name] = f.read_text(encoding="utf-8", errors="replace")[:30000] if f.exists() else ""
    files, total = project_tree(root)
    key = []
    for kf in KEY_FILES:
        p = Path(root).resolve() / kf
        if p.is_file():
            key.append(f"--- {kf}\n{head(p)}")
    snippets = referenced_snippets(root, list(other_texts.values()) + [own_text])

    parts = [
        "You are one agent in a multi-agent mutual code review. Follow the protocol strictly.",
        f"YOUR NAME: {agent}\nYOUR PREFIX: {info['prefix']}\nYOUR FILE: {DIR_NAME}/{info['file']}\n"
        f"ROUND: {round_label}\nCLOCK: {tick_line}\nNOW: {stamp(time.time())}\n"
        f"RING: {ring_text(session)} (you review the next file, answer the previous one; "
        "you do not take part in order negotiation)",
        "PROTOCOL:\n" + (protocol or "(PROTOCOL.md missing)"),
        "LIMITATION: you cannot run commands or open files. Use only the code shown below as evidence. "
        "If a claim cannot be verified from what is shown, mark it KISMEN with 'dogrulanamadi'.",
        f"PROJECT FILES ({total} total, first {len(files)}):\n" + "\n".join(files),
        "KEY FILES:\n" + ("\n\n".join(key) or "(none)"),
    ]
    for name, text in other_texts.items():
        parts.append(f"{' + '.join(roles[name])} - AGENT '{name}' FILE {DIR_NAME}/{others[name]['file']}:\n{text}")
    parts.append("CODE AT REFERENCED path:line LOCATIONS:\n" + (snippets or "(none)"))
    parts.append(f"YOUR CURRENT FILE:\n{own_text}")
    final = round_label == "FINAL"
    parts.append(
        "TASK: " + ("This is the FINAL round: answer pending objections and append the '## Son Durum' block. "
                    if final else "Do this round as the protocol says. ")
        + "Output the COMPLETE new content of YOUR FILE (not a diff), "
        f"between a line '{BEGIN_MARK}' and a line '{END_MARK}'. Nothing else matters outside the markers."
    )
    prompt = "\n\n".join(parts)
    if len(prompt) > max_chars:
        # Drop the project file list first; it is the least valuable context.
        trimmed = f"PROJECT FILES ({total} total): (omitted, prompt too long)"
        prompt = prompt.replace(parts[4], trimmed)
    return prompt[-max_chars:] if len(prompt) > max_chars else prompt


def extract_file(output):
    if BEGIN_MARK in output and END_MARK in output:
        body = output.split(BEGIN_MARK, 1)[1].rsplit(END_MARK, 1)[0]
    else:
        body = output
    body = body.strip()
    fence = re.fullmatch(r"```[a-zA-Z]*\n(.*)\n```", body, re.S)
    if fence:
        body = fence.group(1).strip()
    return body if "## Eksiklikler" in body else None


def run_model(root, args, agent, info, round_label, tick_line):
    session = load_session(root)
    prompt = build_prompt(root, session, agent, info, round_label, tick_line, args.max_chars)
    own = review_dir(root) / info["file"]
    backup = state_dir(root) / "backup"
    backup.mkdir(exist_ok=True)
    print(f"[drive] round {round_label}: prompt {len(prompt)} chars -> {args.exec}", flush=True)
    try:
        res = subprocess.run(args.exec, shell=True, input=prompt, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=parse_duration(args.call_timeout),
                             cwd=Path(root).resolve())
        output, err = res.stdout, (res.stderr or "")[-500:]
    except subprocess.TimeoutExpired:
        output, err = "", "call timed out"
    body = extract_file(output)
    if body is None:
        with own.open("a", encoding="utf-8") as f:
            f.write(f"- Tur {round_label} ({stamp(time.time())}): HATA - model gecerli dosya dondurmedi"
                    f"{' (' + err.strip().splitlines()[-1] + ')' if err.strip() else ''}\n")
        (backup / f"{agent}-{round_label}-raw.txt").write_text(output, encoding="utf-8")
        print(f"[drive] round {round_label}: invalid output, raw saved to {backup}", flush=True)
        return
    (backup / f"{agent}-{round_label}.md").write_text(own.read_text(encoding="utf-8"), encoding="utf-8")
    own.write_text(body + "\n", encoding="utf-8")
    print(f"[drive] round {round_label}: wrote {own}", flush=True)


def cmd_drive(args):
    root = args.root
    _, info = do_join(root, args.agent, args.interval, args.duration, args.file, args.prefix, mode="drive",
                      order_mode=args.order_mode)
    run_model(root, args, args.agent, info, "0", "round 0 (initial findings)")
    while True:
        kind, line = wait_for_tick(root, args.agent)
        print(f"[drive] {line}", flush=True)
        if kind == "SESSION_OVER":
            run_model(root, args, args.agent, info, "FINAL", line)
            break
        run_model(root, args, args.agent, info, line.split()[1], line)
    print("[drive] done", flush=True)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", help="project root (default: active session in cwd, else the one in "
                                   "~/.multi-review/active.json)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def joiner(sp):
        sp.add_argument("--agent", required=True, help="your name: lowercase tool name, e.g. claude, codex")
        sp.add_argument("--interval", help="only used when creating a session")
        sp.add_argument("--duration", help="only used when creating a session")
        sp.add_argument("--order-mode", choices=ORDER_MODES, help="only used when creating a session")
        sp.add_argument("--file", help="own md file name (default: <prefix>.md, e.g. c.md, co.md)")
        sp.add_argument("--prefix", help="finding id prefix (default: auto from name)")

    joiner(sub.add_parser("join"))
    w = sub.add_parser("wait")
    w.add_argument("--agent", required=True)
    w.add_argument("--max-wait", help="return CONTINUE_WAIT after this long (for tools with command timeouts)")
    pr = sub.add_parser("propose")
    pr.add_argument("--agent", required=True)
    pr.add_argument("--order", required=True, help="comma-separated agent names, e.g. claude,codex,antigravity")
    sub.add_parser("shuffle")
    sub.add_parser("roster")
    sub.add_parser("status")
    lv = sub.add_parser("leave")
    lv.add_argument("--agent", required=True)
    sub.add_parser("stop")
    d = sub.add_parser("drive")
    joiner(d)
    d.add_argument("--exec", required=True, help="shell command that reads the prompt on stdin, prints the answer")
    d.add_argument("--call-timeout", default="15m")
    d.add_argument("--max-chars", type=int, default=200000, help="prompt size cap (shrink for small local models)")
    args = p.parse_args()
    creating = args.cmd in ("join", "drive") and bool(getattr(args, "interval", None))
    if creating and not args.root and not is_active(peek_session(Path.cwd())) and registry_active():
        # Creating while another review runs elsewhere: never join that one by accident.
        args.root = str(Path.cwd())
    args.root = resolve_root(args.root)
    {"join": cmd_join, "wait": cmd_wait, "propose": cmd_propose, "shuffle": cmd_shuffle,
     "roster": cmd_roster, "status": cmd_status, "leave": cmd_leave, "stop": cmd_stop,
     "drive": cmd_drive}[args.cmd](args)


if __name__ == "__main__":
    main()
