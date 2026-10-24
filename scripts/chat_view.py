"""`multi_loop.py chat`: a local web page that shows a review session as a chat.

Standard library only. Serves one HTML page and a JSON endpoint on 127.0.0.1;
the page polls /api/state every 2 s. Live mode syncs the event log on each
poll (throttled), replay mode only reads it and plays it back at chat speed.
Nothing leaves the machine: the server binds to localhost and the page loads
no external resources.

The page opens in its own Microsoft Edge app window (`--app`, separate
profile under ~/.multi-review/chat-window). The page holds an SSE connection
to /api/alive; once the window is closed and no page has been connected for
WINDOW_GRACE seconds, the server stops. Without Edge the default browser
opens instead and the server runs until Ctrl+C.
"""
import hmac
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import chat_events

SYNC_EVERY = 2.0
SAY_MAX_BYTES = 16384
WINDOW_GRACE = 8          # seconds without a connected page before an Edge-window server stops
ALIVE_PING = 2            # SSE keep-alive interval; a closed window is noticed on the next write
WINDOW_PROFILE = Path.home() / ".multi-review" / "chat-window"


def find_edge():
    """msedge executable, or None (then the default browser is used)."""
    found = shutil.which("msedge") or shutil.which("microsoft-edge") or shutil.which("microsoft-edge-stable")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            exe = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if exe.is_file():
                return str(exe)
    mac = Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    return str(mac) if mac.is_file() else None


def open_window(url):
    """Edge app window with its own profile (no tabs, no address bar). Returns the process or None."""
    edge = find_edge()
    if not edge:
        return None
    WINDOW_PROFILE.mkdir(parents=True, exist_ok=True)
    args = [edge, f"--app={url}", "--window-size=1280,820", f"--user-data-dir={WINDOW_PROFILE}",
            "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]
    try:
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def make_server(state, describe, sync, port=8777, replay=False, say=None):
    """state: the session's .state folder; describe(): session + agents dict for the page;
    sync(): moves the event log forward (live mode only); say(text): stores a message the user typed
    (None = read-only page). Port 0 picks a free port."""
    token = secrets.token_urlsafe(24)  # only this page knows it: other sites cannot post to the agents
    last_sync = [0.0]
    sync_lock = threading.Lock()
    alive = {"clients": 0, "ever": False, "last": time.time()}
    alive_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, ctype):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "connect-src 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802 - http.server API
            url = urlparse(self.path)
            if url.path == "/":
                page = PAGE.replace("__REPLAY__", "true" if replay else "false")
                page = page.replace("__CAN_SAY__", "true" if say and not replay else "false").replace("__TOKEN__", token)
                self.send(200, page, "text/html; charset=utf-8")
            elif url.path == "/api/alive":
                self.keep_alive()
            elif url.path == "/api/state":
                try:
                    since = max(0, int(parse_qs(url.query).get("since", ["0"])[0]))
                except ValueError:
                    since = 0
                if not replay:
                    with sync_lock:
                        if time.time() - last_sync[0] >= SYNC_EVERY:
                            last_sync[0] = time.time()
                            sync()
                events = chat_events.read_events(state, since)
                body = dict(describe(), events=events, next=since + len(events), replay=replay, now=time.time())
                self.send(200, json.dumps(body, ensure_ascii=False), "application/json; charset=utf-8")
            else:
                self.send(404, "not found", "text/plain; charset=utf-8")

        def do_POST(self):  # noqa: N802 - http.server API
            """The message box. A message reaches AI agents as a request from the user, so only this
            page may send one: same Host and Origin (no DNS rebinding, no cross-site form), JSON body
            (forces a CORS preflight that this server never answers) and the per-run token."""
            here = f"127.0.0.1:{self.server.server_address[1]}"
            if urlparse(self.path).path != "/api/say" or not say or replay:
                return self.send(404, "not found", "text/plain; charset=utf-8")
            ok = (self.headers.get("Host") == here and self.headers.get("Origin") == f"http://{here}"
                  and (self.headers.get("Content-Type") or "").startswith("application/json")
                  and hmac.compare_digest(self.headers.get("X-MR-Token") or "", token))
            if not ok:
                return self.send(403, json.dumps({"error": "forbidden"}), "application/json; charset=utf-8")
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                size = -1
            if not 0 < size <= SAY_MAX_BYTES:
                return self.send(413, json.dumps({"error": "message too large"}), "application/json; charset=utf-8")
            try:
                text = json.loads(self.rfile.read(size).decode("utf-8")).get("text", "")
                msg = say(text if isinstance(text, str) else "")
            except (ValueError, AttributeError) as e:  # bad JSON, empty, too long, session over
                return self.send(400, json.dumps({"error": str(e)}), "application/json; charset=utf-8")
            sync()
            self.send(200, json.dumps({"ok": True, "message": msg}, ensure_ascii=False),
                      "application/json; charset=utf-8")

        def keep_alive(self):
            """SSE stream the page keeps open; it ends when the window (or tab) is closed."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with alive_lock:
                alive["clients"] += 1
                alive["ever"] = True
            try:
                while True:
                    self.wfile.write(b": alive\n\n")
                    self.wfile.flush()
                    time.sleep(ALIVE_PING)
            except OSError:
                pass
            finally:
                with alive_lock:
                    alive["clients"] -= 1
                    alive["last"] = time.time()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True  # open SSE streams must not block shutdown
    server.alive = alive
    return server


def window_closed(alive, now=None):
    """A page was connected, none is now, and that has lasted WINDOW_GRACE seconds (a reload reconnects sooner)."""
    now = now or time.time()
    return alive["ever"] and alive["clients"] <= 0 and now - alive["last"] > WINDOW_GRACE


def serve(state, describe, sync, port=8777, replay=False, open_browser=True, say=None):
    server = make_server(state, describe, sync, port, replay, say)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proc = open_window(url) if open_browser else None
    if open_browser and not proc:
        webbrowser.open(url)
    how = "Edge window; closes with it" if proc else "Ctrl+C to stop"
    print(f"CHAT {'replay' if replay else 'live'} view: {url}  ({how})", flush=True)
    try:
        while not (proc and window_closed(server.alive)):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        server.shutdown()
        server.server_close()
    print("CHAT closed", flush=True)
    return url


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>multi-review chat</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    color-scheme: dark;
    --ink: #0f1115; --side: #0c0e12; --panel: #151820; --panel2: #1b1f29; --line: #2a2f3a;
    --paper: #ece8df; --text: #d9d5cc; --muted: #8d939e; --ok: #7bd88f; --no: #ff6b5e; --pt: #f2c14e;
    --sans: "Segoe UI", system-ui, -apple-system, sans-serif;
    --mono: ui-monospace, "Cascadia Mono", SFMono-Regular, Menlo, Consolas, monospace;
  }
  html, body { height: 100%; }
  body { background: var(--ink); color: var(--paper); font: 14px/1.45 var(--sans); }
  .app { display: grid; grid-template-columns: 250px 1fr; height: 100vh; }
  aside { background: var(--side); border-right: 1px solid var(--line); padding: 20px 16px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }
  .brand { font: 600 17px var(--mono); }
  .brand small, .goal { display: block; font: 400 11px/1.5 var(--mono); color: var(--muted); margin-top: 4px; }
  .goal { color: var(--text); white-space: pre-wrap; max-height: 9em; overflow: hidden; }
  .label { font: 11px var(--mono); color: var(--muted); letter-spacing: .08em; text-transform: uppercase; }
  .agents { display: grid; gap: 6px; }
  .agent { display: flex; align-items: center; gap: 10px; padding: 7px 8px; border-radius: 9px; }
  .agent.typing { background: var(--panel2); }
  .agent.left, .agent.stale { opacity: .4; }
  .av { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; font: 600 11px var(--mono); color: var(--ink); flex: none; }
  .agent b { display: block; font-size: 14px; font-weight: 600; }
  .agent span { font: 11px var(--mono); color: var(--muted); }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); margin-left: auto; flex: none; }
  .agent.stale .dot { background: var(--pt); } .agent.left .dot { background: var(--line); }
  .ring { margin-top: auto; border-top: 1px solid var(--line); padding-top: 12px; font: 11.5px/1.6 var(--mono); color: var(--muted); }
  .ring em { font-style: normal; color: var(--paper); word-break: break-word; }
  main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  header { display: flex; flex-wrap: wrap; gap: 8px 16px; justify-content: space-between; align-items: center; padding: 14px 22px; border-bottom: 1px solid var(--line); font: 12.5px var(--mono); color: var(--muted); }
  header b { color: var(--paper); }
  .clock { color: var(--ok); }
  .note { font: 11.5px var(--mono); color: var(--muted); padding: 8px 22px; border-bottom: 1px solid var(--line); background: var(--side); }
  .feed { flex: 1; overflow-y: auto; padding: 18px 22px 28px; display: flex; flex-direction: column; gap: 10px; }
  .msg { display: flex; gap: 11px; align-items: flex-start; animation: in .3s ease-out; }
  .msg .av { width: 28px; height: 28px; }
  .bubble { background: var(--panel); border: 1px solid var(--line); border-radius: 4px 12px 12px 12px; padding: 9px 13px; max-width: 760px; min-width: 0; }
  .who { font-size: 12.5px; font-weight: 600; margin-bottom: 3px; }
  .who i { font: 400 11px var(--mono); color: var(--muted); margin-left: 8px; font-style: normal; }
  .txt { color: var(--text); overflow-wrap: anywhere; }
  .txt code { font: 12.5px var(--mono); color: var(--paper); background: var(--ink); padding: 1px 5px; border-radius: 4px; }
  .id { font: 600 12.5px var(--mono); color: var(--paper); }
  .v { font: 600 11.5px var(--mono); padding: 1px 7px; border-radius: 4px; color: var(--ink); margin: 0 3px; }
  .v.KABUL { background: var(--ok); } .v.RED, .v.Kritik { background: var(--no); } .v.KISMEN, .v.Yuksek { background: var(--pt); }
  .v.sev { background: var(--line); color: var(--paper); }
  .where { display: block; margin-top: 4px; font: 12px var(--mono); color: var(--muted); }
  s { color: var(--muted); }
  .sys { align-self: center; text-align: center; font: 11.5px var(--mono); color: var(--muted); border: 1px dashed var(--line); padding: 5px 12px; border-radius: 999px; max-width: 90%; animation: in .3s ease-out; }
  .final { border: 1px solid var(--ok); background: rgba(123,216,143,.06); border-radius: 12px; padding: 12px 16px; font: 12.5px/1.65 var(--mono); animation: in .3s ease-out; }
  .final b { color: var(--ok); }
  .typing { display: flex; gap: 11px; align-items: center; }
  .typing .dots { background: var(--panel); border: 1px solid var(--line); border-radius: 4px 12px 12px 12px; padding: 11px 14px; display: flex; gap: 5px; }
  .typing .dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); animation: blink 1.2s infinite; }
  .typing .dots i:nth-child(2) { animation-delay: .2s; } .typing .dots i:nth-child(3) { animation-delay: .4s; }
  .msg.you { flex-direction: row-reverse; }
  .msg.you .bubble { background: #1d2a22; border-color: #2f4a38; border-radius: 12px 4px 12px 12px; }
  .msg.you .av { background: var(--ok); }
  .compose { display: flex; gap: 10px; padding: 12px 22px 16px; border-top: 1px solid var(--line); background: var(--side); align-items: flex-end; }
  .compose .col { flex: 1; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .compose textarea { resize: none; min-height: 42px; max-height: 160px; font: 14px/1.4 var(--sans); color: var(--paper); background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; }
  .compose textarea:focus { outline: 2px solid var(--ok); outline-offset: -1px; }
  .compose button { padding: 10px 16px; font-size: 13px; background: var(--ok); color: var(--ink); border: 0; font-weight: 600; }
  .compose button:disabled, .compose textarea:disabled { opacity: .5; cursor: default; }
  .compose .hint { font: 11px var(--mono); color: var(--muted); }
  .empty { margin: auto; color: var(--muted); font: 12.5px var(--mono); text-align: center; }
  button { font: 12px var(--mono); color: var(--paper); background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; padding: 4px 10px; cursor: pointer; }
  @keyframes in { from { opacity: 0; transform: translateY(6px); } }
  @keyframes blink { 50% { opacity: .25; } }
  @media (prefers-reduced-motion: reduce) { * { animation: none !important; } }
  @media (max-width: 720px) {
    .app { grid-template-columns: 1fr; grid-template-rows: auto 1fr; height: 100vh; }
    aside { border-right: 0; border-bottom: 1px solid var(--line); flex-direction: row; flex-wrap: wrap; max-height: 40vh; }
    .agents { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); width: 100%; }
    .ring { margin-top: 0; width: 100%; }
    aside .label { display: none; }
  }
</style>
</head>
<body>
<div class="app">
  <aside>
    <div><div class="brand">multi-review<small id="meta">loading…</small></div><span class="goal" id="goal"></span></div>
    <div class="label">agents</div>
    <div class="agents" id="agents"></div>
    <div class="ring" id="ring"></div>
  </aside>
  <main>
    <header><span><b id="title">#review</b> &nbsp;every finding needs path:line</span><span><span class="clock" id="clock"></span> <button id="restart" hidden>replay again</button></span></header>
    <div class="note">Built from the agents' review files, not a transcript of what they said.</div>
    <div class="feed" id="feed"><div class="empty" id="empty">waiting for the first event…</div></div>
    <form class="compose" id="compose" hidden>
      <div class="col">
        <textarea id="say" rows="1" maxlength="2000" placeholder="Message the agents… (@codex to address one)" aria-label="Message the agents"></textarea>
        <span class="hint" id="hint">Enter sends · Shift+Enter new line · agents pick it up within ~10 s and answer in their file</span>
      </div>
      <button type="submit" id="send">Send</button>
    </form>
  </main>
</div>
<script>
"use strict";
const REPLAY = __REPLAY__, CAN_SAY = __CAN_SAY__, TOKEN = "__TOKEN__";
const KNOWN = { claude: "#e8a87c", antigravity: "#8ab4f8", codex: "#c6c1b5", gemini: "#b39ddb", cursor: "#f48fb1",
                copilot: "#90caf9", opencode: "#a5d6a7", qwen: "#7fd1c7" };
const PALETTE = ["#f6c177", "#9ccfd8", "#c4a7e7", "#ebbcba", "#a3be8c", "#88c0d0", "#d08770", "#b48ead"];
let state = null, cursor = 0, queue = [], playing = false, shown = 0;
const seenAgents = new Set();  // replay: agents that have joined so far
const feed = document.getElementById("feed");

function color(name) {
  if (KNOWN[name]) return KNOWN[name];
  let h = 0; for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
// `code` spans become <code>; everything else stays text (agent files are untrusted input).
function rich(parent, text) {
  String(text || "").split("`").forEach((part, i) => { if (part) parent.appendChild(i % 2 ? el("code", null, part) : document.createTextNode(part)); });
  return parent;
}
function avatar(name) {
  const a = el("div", "av", (state && state.agents.find((x) => x.name === name) || {}).prefix || name.slice(0, 2).toUpperCase());
  a.style.background = color(name); return a;
}
function sevClass(sev) { return ["Kritik", "Yuksek"].includes(sev) ? sev : "sev"; }
function badge(word, extra) { return el("span", "v " + (extra || word), word); }
function hhmm(t) { return t ? new Date(t * 1000).toTimeString().slice(0, 5) : ""; }
function fmt(s) { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return (h ? h + "h" : "") + String(m).padStart(h ? 2 : 1, "0") + "m"; }

function message(e, body) {
  const m = el("div", "msg"), b = el("div", "bubble"), who = el("div", "who", e.agent);
  who.style.color = color(e.agent); who.appendChild(el("i", null, hhmm(e.t)));
  b.append(who, body); m.append(avatar(e.agent), b); return m;
}
function render(e) {
  const txt = el("div", "txt");
  switch (e.kind) {
    case "session": return el("div", "sys", `session created · ${e.order_mode} ring · ${e.work_mode}` + (e.goal ? " · goal set" : ""));
    case "join": return el("div", "sys", `${e.agent} joined · ${e.file}` + (e.mode === "drive" ? " · text-only model" : ""));
    case "leave": return el("div", "sys", `${e.agent} left`);
    case "ring": return el("div", "sys", `ring v${e.version}: ${e.ring}`);
    case "final_phase": return el("div", "sys", "final phase · the final writer drafts final.md");
    case "stopped": return el("div", "sys", "session over");
    case "final": {
      const f = el("div", "final"); f.appendChild(el("b", null, "final.md"));
      (e.items || []).forEach((it) => { f.appendChild(document.createElement("br")); f.append(it.id + " "); if (it.severity) f.appendChild(badge(it.severity, sevClass(it.severity))); rich(f, " " + it.text); });
      if (!(e.items || []).length) f.append(" (no items)");
      return f;
    }
    case "finding":
      txt.appendChild(el("span", "id", e.id)); if (e.severity) txt.appendChild(badge(e.severity, sevClass(e.severity)));
      rich(txt, " " + e.text); if (e.where) rich(txt.appendChild(el("span", "where")), e.where);
      return message(e, txt);
    case "withdraw":
      txt.appendChild(el("s", null, e.id)); rich(txt, " withdrawn" + (e.text ? ": " + e.text : "")); return message(e, txt);
    case "verdict":
      txt.appendChild(el("span", "id", e.id)); txt.appendChild(badge(e.verdict));
      if (e.note) txt.append(`(${e.note}) `); rich(txt, e.text);
      if (e.target) txt.appendChild(el("span", "where", "reviewing " + e.target));
      return message(e, txt);
    case "user": {
      const m = el("div", "msg you"), b = el("div", "bubble"), who = el("div", "who", "you");
      who.style.color = "var(--ok)"; who.appendChild(el("i", null, `${e.id} → ${e.to === "all" ? "all agents" : "@" + e.to} · ${hhmm(e.t)}`));
      b.append(who, rich(el("div", "txt"), e.text)); m.append(el("div", "av", "YOU"), b); return m;
    }
    case "reply":
      txt.appendChild(el("span", "id", "↪ " + e.id + " ")); rich(txt, e.text); return message(e, txt);
    case "answer":
      txt.appendChild(el("span", "id", e.id));
      if (e.objection) txt.append(` ${e.objection}${e.by ? " by " + e.by : ""} → `);
      txt.appendChild(badge(e.verdict)); rich(txt, e.text); return message(e, txt);
  }
  return null;
}
function atBottom() { return feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80; }
function push(node) {
  if (!node) return;
  const stick = atBottom(); document.getElementById("empty")?.remove();
  feed.insertBefore(node, document.getElementById("typing")); if (stick) feed.scrollTop = feed.scrollHeight;
}
function typing(names) {
  document.getElementById("typing")?.remove();
  if (!names.length) return;
  const t = el("div", "typing"); t.id = "typing";
  names.slice(0, 3).forEach((n) => t.appendChild(avatar(n)));
  const d = el("div", "dots"); d.append(el("i"), el("i"), el("i")); t.appendChild(d);
  const stick = atBottom(); feed.appendChild(t); if (stick) feed.scrollTop = feed.scrollHeight;
}

function sidebar(speaking) {
  const s = state.session, box = document.getElementById("agents");
  document.getElementById("title").textContent = "#" + s.name;
  document.getElementById("meta").textContent = `${s.order_mode} · ${s.work_mode} · every ${fmt(s.interval)}`;
  document.getElementById("goal").textContent = s.goal ? "goal: " + s.goal : "";
  box.replaceChildren(...state.agents.map((a) => {
    const gone = REPLAY ? !seenAgents.has(a.name) : a.left;
    const row = el("div", "agent" + (gone ? " left" : !REPLAY && a.stale ? " stale" : "") + (speaking.includes(a.name) ? " typing" : ""));
    const info = el("div"); info.append(el("b", null, a.name), el("span", null, (a.mode === "drive" ? "text-only · " : "") + a.file));
    row.append(avatar(a.name), info, el("div", "dot")); return row;
  }));
  const ring = document.getElementById("ring"); ring.replaceChildren(el("span", null, `RING v${s.ring_version}`), document.createElement("br"), el("em", null, s.ring));
}
function clock(t) {
  const s = state.session, c = document.getElementById("clock");
  if (REPLAY) c.textContent = `replay · ${shown} / ${shown + queue.length} events`;
  else c.textContent = s.active ? `${fmt(state.now - s.start)} elapsed · ${fmt(s.end - state.now)} left` : "session over";
}

async function poll() {
  try {
    const r = await fetch("/api/state?since=" + cursor, { cache: "no-store" });
    state = await r.json(); cursor = state.next;
    if (REPLAY) { queue.push(...state.events); if (!playing) play(); }
    else { state.events.forEach((e) => push(render(e))); const live = state.agents.filter((a) => a.typing).map((a) => a.name); sidebar(live); typing(live); clock(); }
  } catch (err) { document.getElementById("clock").textContent = "server not reachable"; }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function play() {
  playing = true; sidebar([]);
  while (queue.length) {
    const e = queue.shift();
    if (e.kind === "join") seenAgents.add(e.agent); else if (e.kind === "leave") seenAgents.delete(e.agent);
    if (e.kind === "ring") { state.session.ring = e.ring; state.session.ring_version = e.version; }
    const chat = !!e.agent && !["join", "leave"].includes(e.kind);
    if (chat) { sidebar([e.agent]); typing([e.agent]); await sleep(700); typing([]); }
    shown += 1; push(render(e)); clock(); sidebar([]); await sleep(chat ? 650 : 350);
  }
  playing = false; document.getElementById("restart").hidden = false;
}
document.getElementById("restart").onclick = () => { feed.replaceChildren(); seenAgents.clear(); shown = 0; cursor = 0; queue = []; document.getElementById("restart").hidden = true; poll(); };
const form = document.getElementById("compose"), box = document.getElementById("say"), sendBtn = document.getElementById("send");
async function send() {
  const text = box.value.trim(); if (!text) return;
  sendBtn.disabled = true;
  try {
    const r = await fetch("/api/say", { method: "POST", headers: { "Content-Type": "application/json", "X-MR-Token": TOKEN }, body: JSON.stringify({ text }) });
    const out = await r.json();
    if (!r.ok) throw new Error(out.error || r.status);
    box.value = ""; box.style.height = "auto";
    document.getElementById("hint").textContent = `${out.message.id} sent to ${out.message.to === "all" ? "all agents" : "@" + out.message.to} · waiting for answers`;
    feed.scrollTop = feed.scrollHeight; poll();
  } catch (err) { document.getElementById("hint").textContent = "not sent: " + err.message; }
  sendBtn.disabled = false; box.focus();
}
form.onsubmit = (ev) => { ev.preventDefault(); send(); };
box.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); send(); } });
box.addEventListener("input", () => { box.style.height = "auto"; box.style.height = Math.min(box.scrollHeight, 160) + "px"; });
form.hidden = !CAN_SAY;
setInterval(() => {
  if (!state || !CAN_SAY || state.session.active) return;
  box.disabled = sendBtn.disabled = true; box.placeholder = "The session is over.";
}, 1000);
poll(); if (!REPLAY) setInterval(poll, 2000);
new EventSource("/api/alive");  // keeps the server up while this window is open
</script>
</body>
</html>
"""
