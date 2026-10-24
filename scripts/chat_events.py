"""Event log for the live chat view of a multi-review session.

Agents overwrite their markdown files, so nothing records *when* a finding or
a verdict appeared. `sync` compares the session and every agent file with what
it saw last time and appends the new items to <state>/events.jsonl, one JSON
object per line. Anything that calls `sync` (every `wait` tick, join, leave,
finalize, and the chat server while it runs) moves the log forward; the
first caller wins, the others see nothing new.

Events are derived from the files, not a transcript of the agents talking:
  session   the session was created (goal, order and work mode)
  join      an agent joined (or rejoined)          leave  an agent left
  ring      the review ring changed
  finding   a new `### <PREFIX>-NNN [Severity] title` heading in an agent file
  withdraw  `~~<ID>~~` on one of the agent's own ids
  verdict   `<ID> ... KABUL|RED|KISMEN ...` line under `## Incelemeler`
  answer    the same on an own id under `## Bana gelen itirazlara cevap`
  final_phase / final / stopped
Standard library only (Python 3.8+), like multi_loop.py.
"""
import json
import os
import re
import time
from pathlib import Path

EVENTS_FILE = "events.jsonl"
SEEN_FILE = "events.seen.json"
LOCK_FILE = "events.lock"
LOCK_STALE = 30          # seconds; a crashed syncer must not block the log forever
TEXT_CAP = 280
ORDER = ["session", "join", "leave", "ring", "user", "reply", "finding", "verdict", "answer", "withdraw",
         "final_phase", "final", "stopped"]

ID_RE = re.compile(r"\b([A-Z][A-Z0-9]{0,3}-\d{1,4})\b")
VERDICT_RE = re.compile(r"\b(KABUL|RED|KISMEN)\b")
STRIKE_RE = re.compile(r"~~\s*([A-Z][A-Z0-9]{0,3}-\d{1,4})\s*~~")
FINAL_ITEM_RE = re.compile(r"^###\s+(F-\d+)\s*(?:\[([^\]]+)\])?\s*(.*)$", re.M)
USER_REPLY_RE = re.compile(r"^\s*[-*]\s*\**\s*(U-\d+)\s*\**\s*[:.\-—]+\s*(.+)$")
USER_FILE = "user.jsonl"  # written by multi_loop.post_user_message (the chat page's message box)


def _clean(text):
    """One line of markdown -> short plain text (backticks kept: the view renders them as code)."""
    text = re.sub(r"\*\*|__", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:TEXT_CAP - 1] + "…" if len(text) > TEXT_CAP else text


def _section(heading):
    """Which part of an agent file a `## ` heading opens."""
    h = heading.lower()
    if "incelemeler" in h:
        return "reviews"
    if "bana gelen" in h:
        return "answers"
    if "kullaniciya" in h or "kullanıcıya" in h:
        return "user"
    return "other"


def parse_agent_file(text, prefix):
    """All chat items in one agent file, keyed so that a later sync only reports new ones."""
    items = {}
    own = re.compile(rf"^{re.escape(prefix)}-\d+$")
    heading = re.compile(rf"^###\s+(~~)?\s*({re.escape(prefix)}-\d+)\s*(~~)?\s*(?:\[([^\]]+)\])?\s*(.*)$")
    section, target = "other", None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            section, target = _section(line[3:]), None
            continue
        if line.startswith("### "):
            m = heading.match(line)
            if m:
                fid = m.group(2)
                where = _where(lines, i + 1)
                items[f"finding:{fid}"] = {"kind": "finding", "id": fid, "severity": (m.group(4) or "").strip(),
                                           "text": _clean(m.group(5)), "where": where}
                if m.group(1) or m.group(3):
                    items[f"withdraw:{fid}"] = {"kind": "withdraw", "id": fid, "text": ""}
            elif section == "reviews":
                target = _clean(line[4:].replace("incelemesi", "").strip()) or None
            continue
        for sid in STRIKE_RE.findall(line):
            if own.match(sid):
                rest = line.split("~~", 2)[-1]
                items[f"withdraw:{sid}"] = {"kind": "withdraw", "id": sid, "text": _clean(rest.strip(" :-()"))}
        if section == "user":
            m = USER_REPLY_RE.match(line)
            if m:
                # One reply per message; editing it later updates nothing (the first answer stays in the log).
                items[f"reply:{m.group(1)}"] = {"kind": "reply", "id": m.group(1), "text": _clean(m.group(2))}
            continue
        if section not in ("reviews", "answers") or not line.lstrip().startswith(("-", "*")):
            continue
        mid = ID_RE.search(line)
        if not mid:
            continue
        mv = VERDICT_RE.search(line, mid.end())
        if not mv:
            continue
        fid = mid.group(1)
        if section == "reviews" and not own.match(fid):
            note, reason = _after(line, mv.end())
            items[f"verdict:{fid}:{mv.group(1)}"] = {"kind": "verdict", "id": fid, "verdict": mv.group(1),
                                                    "note": note, "text": reason, "target": target}
        elif section == "answers" and own.match(fid):
            # "C-002 KISMEN (CO): KABUL. ..." quotes the objection first; the reply follows the colon.
            note, rest = _after(line, mv.end())
            reply = VERDICT_RE.match(rest)
            word, objection = (reply.group(1), mv.group(1)) if reply else (mv.group(1), None)
            text = _clean(rest[reply.end():].lstrip(" .,:;-—")) if reply else rest
            items[f"answer:{fid}:{word}"] = {"kind": "answer", "id": fid, "verdict": word, "objection": objection,
                                            "by": note, "text": text}
    return items


def _after(line, pos):
    """Text after a verdict word: an optional `(note)` (severity or who objected), then the reason."""
    m = re.match(r"\s*(?:\(([^)]*)\))?\s*[-:—.,;]*\s*", line[pos:])
    return (m.group(1) or "").strip(), _clean(line[pos + m.end():])


def _where(lines, start):
    """The `Yer:` reference of a finding (first `path:line` of its body), if any."""
    for line in lines[start:start + 8]:
        if line.startswith("#"):
            break
        m = re.match(r"\s*[-*]\s*Yer\s*:\s*(.+)", line, re.I)
        if m:
            return _clean(m.group(1))[:120]
    return ""


def final_items(text):
    return [{"id": m.group(1), "severity": (m.group(2) or "").strip(), "text": _clean(m.group(3))}
            for m in FINAL_ITEM_RE.finditer(text)]


def ring_text(session):
    agents = session.get("agents", {})
    ring = [n for n in session.get("ring", []) if n in agents and not agents[n].get("left")]
    return " > ".join(ring + ring[:1]) if len(ring) > 1 else (ring[0] if ring else "-")


class _SyncLock:
    def __init__(self, path):
        self.path = path
        self.ok = False

    def __enter__(self):
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            self.ok = True
        except FileExistsError:
            try:
                if time.time() - self.path.stat().st_mtime > LOCK_STALE:
                    self.path.unlink()
            except OSError:
                pass
        return self

    def __exit__(self, *exc):
        if self.ok:
            try:
                self.path.unlink()
            except OSError:
                pass


def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def sync(state, review, session, final_name="final.md", now=None):
    """Append every new item since the last sync to events.jsonl. Returns how many were added.

    state/review: the session's .state and review folders. Never raises on a busy log: another
    process is syncing the same changes right now."""
    if not session:
        return 0
    state, review = Path(state), Path(review)
    now = now or time.time()
    with _SyncLock(state / LOCK_FILE) as lock:
        if not lock.ok:
            return 0
        seen_path = state / SEEN_FILE
        seen = _load(seen_path, {})
        if seen.get("start") != session.get("start"):
            seen = {"start": session.get("start"), "keys": []}
        known = set(seen["keys"])
        new = []

        def add(key, event, at=None):
            if key not in known:
                known.add(key)
                new.append(dict(event, t=round(at or now, 3)))

        add("session", {"kind": "session", "goal": session.get("goal"), "order_mode": session.get("order_mode"),
                        "work_mode": session.get("work_mode", "research")}, session.get("start"))
        agents = session.get("agents", {})
        for name, a in sorted(agents.items(), key=lambda kv: kv[1].get("joined", 0)):
            add(f"join:{name}:{a.get('joined')}", {"kind": "join", "agent": name, "file": a.get("file"),
                                                   "prefix": a.get("prefix"), "mode": a.get("mode", "agent")},
                a.get("joined"))
        presence = seen.setdefault("presence", {})
        for name, a in agents.items():
            state_now = "left" if a.get("left") else "in"
            if presence.get(name) != state_now:
                if presence.get(name) == "in" and state_now == "left":
                    add(f"leave:{name}:{len(known)}", {"kind": "leave", "agent": name})
                elif presence.get(name) == "left" and state_now == "in":
                    add(f"rejoin:{name}:{len(known)}", {"kind": "join", "agent": name, "file": a.get("file"),
                                                        "prefix": a.get("prefix"), "mode": a.get("mode", "agent")})
                presence[name] = state_now
        add(f"ring:{session.get('ring_version')}", {"kind": "ring", "ring": ring_text(session),
                                                    "version": session.get("ring_version")})
        for msg in read_events(state, 0, USER_FILE):
            add(f"user:{msg.get('id')}", {"kind": "user", "id": msg.get("id"), "to": msg.get("to", "all"),
                                          "text": _clean(str(msg.get("text", "")))}, msg.get("t"))
        for name, a in agents.items():
            f = review / a["file"]
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for key, item in parse_agent_file(text, a["prefix"]).items():
                add(f"{name}:{key}", dict(item, agent=name))
        stopped = session.get("stopped")
        if not stopped and session.get("end", 0) > now and session["end"] - now <= session.get("interval", 0):
            add("final_phase", {"kind": "final_phase"})
        final = review / final_name
        if session.get("finalized") and final.is_file():
            add("final", {"kind": "final", "items": final_items(final.read_text(encoding="utf-8", errors="replace"))})
        elif stopped or session.get("end", 0) <= now:
            add("stopped", {"kind": "stopped"})
        # One sync can pick up a whole tick at once: show it in reading order, findings before verdicts.
        new.sort(key=lambda e: (e["t"], ORDER.index(e["kind"])))
        if new:
            with open(state / EVENTS_FILE, "a", encoding="utf-8") as fh:
                for e in new:
                    fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        seen["keys"] = sorted(known)
        tmp = seen_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, seen_path)
        return len(new)


def read_events(state, since=0, name=EVENTS_FILE):
    """Lines of a .jsonl log from index `since` on; a line still being written is skipped until complete."""
    path = Path(state) / name
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[since:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            break
    return out


def reset(state, archive_dir=None):
    """A new session starts with an empty log; the old one moves next to the archived md files."""
    state = Path(state)
    for name in (EVENTS_FILE, SEEN_FILE, USER_FILE):
        f = state / name
        if not f.exists():
            continue
        if archive_dir:
            Path(archive_dir).mkdir(parents=True, exist_ok=True)
            os.replace(f, Path(archive_dir) / name)
        else:
            f.unlink()
