"""The chat panel's side of multi-review: one hub for every session on this machine.

`join` calls open_chat_hub() when it creates a session; `chat` and `rename` are the CLI commands.
ChatHub gives chat_view.py what it needs per session key ("<root>|<session>"): the session list
(running from the registry, finished from recent.json, plus every session folder of a known
project), the page data, the event-log sync, the message box and the display name.
Standard library only, like multi_loop.py.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import multi_loop as ml

TYPING_WINDOW = 15  # seconds since an agent file changed that count as "typing"


def describe(root):
    """Session and agents as the chat page shows them (chat_view.py)."""
    session = ml.load_session(root) or {}
    now = time.time()
    agents = []
    for name, a in session.get("agents", {}).items():
        f = ml.review_dir(root) / a["file"]
        mtime = f.stat().st_mtime if f.exists() else 0
        agents.append({"name": name, "file": a["file"], "prefix": a["prefix"], "mode": a.get("mode", "agent"),
                       "left": bool(a.get("left")), "dropped": a.get("left_reason") == "silent",
                       "stale": bool(session) and ml.is_active(session, now) and ml.is_stale(root, session, name, now),
                       "typing": ml.is_active(session, now) and not a.get("left") and now - mtime < TYPING_WINDOW})
    return {"session": {"name": ml.current_session(), "project": Path(root).resolve().name,
                        "title": session.get("title"),
                        "goal": session.get("goal"),
                        "start": session.get("start", now), "end": session.get("end", now),
                        "interval": session.get("interval", 0), "order_mode": session.get("order_mode"),
                        "work_mode": session.get("work_mode", "research"), "active": ml.is_active(session, now),
                        "ring": ml.ring_text(session) if session else "-",
                        "ring_version": session.get("ring_version", 0)},
            "agents": agents}


RECENT_KEEP = 30
AUTO_CHAT = os.environ.get("MULTI_REVIEW_NO_CHAT") != "1"  # tests and CI set it; `join` then opens nothing


def recent_path():
    return ml.REGISTRY.with_name("recent.json")


def hub_path():
    return ml.REGISTRY.with_name("hub.json")


def remember_session(root):
    """Finished sessions leave the registry; the hub still lists the last RECENT_KEEP of them."""
    root = str(Path(root).resolve())
    with ml.registry_lock():
        try:
            old = ml.load_json(recent_path(), []) or []
        except (OSError, ValueError):
            old = []
        keep = [e for e in old if not ml.same_session(e, root, ml.current_session())][-(RECENT_KEEP - 1):]
        ml.save_json(recent_path(), keep + [{"root": root, "session": ml.current_session(), "created": time.time()}])


TITLE_MAX = 60


def set_title(root, title):
    """Display name of a session (chat panel, `rename`). Only a label: the session's name, folder and
    every NEXT: command stay the same, so a running review is not disturbed. Empty = no title."""
    title = " ".join((title or "").split())  # one line, no control characters
    if len(title) > TITLE_MAX:
        raise ValueError(f"name longer than {TITLE_MAX} characters")
    with ml.Lock(root):
        session = ml.load_session(root)
        if not session:
            raise ValueError("no such session")
        if title:
            session["title"] = title
        else:
            session.pop("title", None)
        ml.save_json(ml.session_path(root), session)
    return title or None


def cmd_rename(args):
    if not ml.load_session(args.root):
        sys.exit("NO_SESSION")
    try:
        title = set_title(args.root, args.title)
    except ValueError as e:
        sys.exit(f"ERROR: {e}")
    print(f"RENAMED session '{ml.current_session()}' -> {title or '(no title)'}")


def session_key(root, session):
    return f"{Path(root).resolve()}|{session}"


def ended_at(entry, s, now):
    """When a session ended (or will end): `stop`/`finalize` before the planned end record the time;
    older sessions stopped early fall back to when their session.json was last written."""
    if s.get("stopped_at"):
        return s["stopped_at"]
    if s.get("stopped") and s.get("end", 0) > now:
        try:
            folder = ml.session_rel(entry.get("session", ml.DEFAULT_SESSION))
            p = Path(entry["root"]) / folder / ml.STATE_DIR / "session.json"
            return p.stat().st_mtime
        except OSError:
            return now
    return s.get("end", 0)


class ChatHub:
    """What chat_view.py needs, for every known session (running: registry; finished: recent.json)."""

    def sessions(self):
        try:
            recent = ml.load_json(recent_path(), []) or []
        except (OSError, ValueError):
            recent = []
        seen, out, now = set(), [], time.time()
        entries = list(ml.registry_active()) + list(reversed(recent))
        for root in {str(Path(e["root"]).resolve()) for e in entries}:
            # Every session of a known project (main + named), also ones created before recent.json existed.
            if (Path(root) / ml.DIR_NAME / ml.STATE_DIR / "session.json").is_file():
                entries.append({"root": root, "session": ml.DEFAULT_SESSION})
            for f in sorted((Path(root) / ml.DIR_NAME).glob(f"*/{ml.STATE_DIR}/session.json")):
                entries.append({"root": root, "session": f.parent.parent.name})
        for e in entries:
            key = session_key(e["root"], e.get("session", ml.DEFAULT_SESSION))
            s = ml.peek_session(e["root"], e.get("session", ml.DEFAULT_SESSION))
            if key in seen or not s:
                continue
            seen.add(key)
            out.append({"key": key, "root": str(Path(e["root"]).resolve()),
                        "session": e.get("session", ml.DEFAULT_SESSION),
                        "project": Path(e["root"]).resolve().name, "title": s.get("title"),
                        "goal": s.get("goal"),
                        "active": ml.is_active(s, now), "start": s.get("start", 0), "end": ended_at(e, s, now),
                        "agents": len(ml.active_agents(s))})
        out.sort(key=lambda x: (not x["active"], -x["start"]))
        return out

    def _run(self, key, fn):
        root, _, session = key.rpartition("|")
        with ml.using_session(session):
            return fn(root)

    def state_dir(self, key):
        return self._run(key, ml.state_dir)

    def describe(self, key):
        return self._run(key, describe)

    def sync(self, key):
        return self._run(key, ml.log_events)

    def say(self, key, text):
        return self._run(key, lambda root: ml.post_user_message(root, text))

    def rename(self, key, title):
        return self._run(key, lambda root: set_title(root, title))


def hub_running():
    """Port of a live chat hub on this machine, else None."""
    try:
        port = int((ml.load_json(hub_path(), {}) or {}).get("port", 0))
        if not port:
            return None
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return port if json.loads(r.read().decode("utf-8")).get("app") == "multi-review-chat" else None
    except (OSError, ValueError):
        return None


def open_chat_hub(root):
    """Called when a session is created: the user sees the chat without asking for it."""
    if not AUTO_CHAT:
        return
    port = hub_running()
    if port:
        print(f"CHAT_PANEL already open: http://127.0.0.1:{port}/ (it switches to this session by itself)")
        return
    args = [sys.executable, str(Path(__file__).resolve()), "--root", str(Path(root).resolve()),
            "--session", ml.current_session(), "chat"]
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "cwd": str(Path(root).resolve())}
    if os.name == "nt":
        # Own process group, no console, and out of the calling tool's job so it outlives this command.
        flags = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        attempts = [dict(kw, creationflags=flags | 0x01000000), dict(kw, creationflags=flags)]  # + BREAKAWAY
    else:
        attempts = [dict(kw, start_new_session=True)]
    for extra in attempts:
        try:
            subprocess.Popen(args, **extra)
            print("CHAT_PANEL opening: the live chat of this session (Edge window, closes with it)")
            return
        except OSError:
            continue
    print("CHAT_PANEL could not start; run: " + ml.next_cmd(root, "chat").split("NEXT: ", 1)[1])


def cmd_chat(args):
    import chat_view  # only the chat command needs the web server
    port = hub_running()
    if port and not args.no_open:
        print(f"CHAT_PANEL already open: http://127.0.0.1:{port}/ (pick the session in its list)")
        return
    initial = session_key(args.root, ml.current_session()) if ml.peek_session(args.root, ml.current_session()) else None
    if initial:
        ml.log_events(args.root)
        remember_session(args.root)  # sessions from before 0.6.1 are not in recent.json yet
    chat_view.serve(ChatHub(), port=args.port, initial=initial, replay_first=args.replay,
                    open_browser=not args.no_open, hub_file=None if args.no_open else hub_path())
