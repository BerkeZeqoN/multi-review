"""`multi_loop.py chat`: one local web page for every multi-review session on this machine.

Standard library only. The page lists the sessions (running ones first, then the
recently finished ones), shows the selected one as a chat and lets the user write
to its agents. A running session is shown live (the page polls /api/state every
2 s; each poll moves the event log forward, throttled), a finished one is played
back from its event log at chat speed. Nothing leaves the machine: the server
binds to 127.0.0.1, checks the Host header on every request, and the page loads
no external resources.

It is a single hub: `join` starts it when a session is created and no hub runs
yet (see ~/.multi-review/hub.json); an open page picks up new sessions by itself.
The page opens in its own Microsoft Edge app window (`--app`, separate profile
under ~/.multi-review/chat-window) and holds an SSE connection to /api/alive;
once the window is closed and no page has been connected for WINDOW_GRACE
seconds, the hub stops. Without Edge the default browser opens instead and the
hub runs until Ctrl+C.
"""
import hmac
import json
import os
import secrets
import shutil
import socket
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
PORT_TRIES = 20           # 8777, 8778, ... when the default port is taken
WINDOW_GRACE = 8          # seconds without a connected page before an Edge-window hub stops
ALIVE_PING = 2            # SSE keep-alive interval; a closed window is noticed on the next write
WINDOW_PROFILE = Path.home() / ".multi-review" / "chat-window"
APP_ID = "multi-review-chat"
# The page (HTML, CSS, JS) lives next to this file; placeholders __TOKEN__, __INITIAL__ and
# __REPLAY_FIRST__ are filled per request.
PAGE = (Path(__file__).resolve().parent / "chat_page.html").read_text(encoding="utf-8")


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
    args = [edge, f"--app={url}", "--window-size=1320,840", f"--user-data-dir={WINDOW_PROFILE}",
            "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]
    try:
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


class Server(ThreadingHTTPServer):
    # Windows' SO_REUSEADDR lets a second server bind a port that is in use; then two hubs would
    # share one port. Bind exclusively there, so a busy port fails and bind() moves to the next one.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True  # open SSE streams must not block shutdown

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(hub, port=8777, initial=None, replay_first=False):
    """hub: sessions() -> [{"key", ...}], and per key: state_dir(key), describe(key), sync(key),
    say(key, text) (say may raise ValueError). Only keys that sessions() lists are ever served,
    so a request cannot point the server at an arbitrary folder. Port 0 picks a free port."""
    token = secrets.token_urlsafe(24)  # only this page knows it: other sites cannot post to the agents
    last_sync, sync_lock = {}, threading.Lock()
    alive = {"clients": 0, "ever": False, "last": time.time()}
    alive_lock = threading.Lock()

    def known(key):
        return any(s["key"] == key for s in hub.sessions())

    def sync_due(key):
        with sync_lock:
            if time.time() - last_sync.get(key, 0) < SYNC_EVERY:
                return False
            last_sync[key] = time.time()
            return True

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, ctype="application/json; charset=utf-8"):
            data = (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode("utf-8")
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

        def host_ok(self):
            # DNS rebinding: a page on another domain that resolves to 127.0.0.1 still sends its own Host.
            port = self.server.server_address[1]
            return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def do_GET(self):  # noqa: N802 - http.server API
            if not self.host_ok():
                return self.send(403, {"error": "forbidden"})
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path == "/":
                page = (PAGE.replace("__TOKEN__", token)
                        .replace("__INITIAL__", json.dumps(initial or ""))
                        .replace("__REPLAY_FIRST__", "true" if replay_first else "false"))
                self.send(200, page, "text/html; charset=utf-8")
            elif url.path == "/api/ping":
                self.send(200, {"app": APP_ID, "pid": os.getpid()})
            elif url.path == "/api/alive":
                self.keep_alive()
            elif url.path == "/api/sessions":
                self.send(200, {"sessions": hub.sessions(), "now": time.time()})
            elif url.path == "/api/state":
                key = q.get("s", [""])[0]
                if not known(key):
                    return self.send(404, {"error": "unknown session"})
                try:
                    since = max(0, int(q.get("since", ["0"])[0]))
                except ValueError:
                    since = 0
                info = hub.describe(key)
                state = hub.state_dir(key)
                # A running session moves its log forward; a finished one from before the log existed
                # gets one built once from what is left (session.json, final.md, any agent files).
                fresh_log = not (Path(state) / chat_events.EVENTS_FILE).exists()
                if (info["session"]["active"] or fresh_log) and sync_due(key):
                    hub.sync(key)
                events = chat_events.read_events(state, since)
                self.send(200, dict(info, events=events, next=since + len(events), now=time.time()))
            else:
                self.send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802 - http.server API
            """Message box (/api/say) and session name (/api/rename). A message reaches AI agents as a
            request from the user, so only this page may write: same Host and Origin (no DNS rebinding,
            no cross-site form), JSON body (forces a CORS preflight that this server never answers) and
            the per-run token."""
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                size = -1
            if not 0 < size <= SAY_MAX_BYTES:
                self.close_connection = True
                return self.send(413, {"error": "message too large"})
            raw = self.rfile.read(size)  # read before any answer: an unread body resets the connection
            action = {"/api/say": "say", "/api/rename": "rename"}.get(urlparse(self.path).path)
            if not action:
                return self.send(404, {"error": "not found"})
            port = self.server.server_address[1]
            ok = (self.host_ok() and self.headers.get("Origin") in (f"http://127.0.0.1:{port}",
                                                                     f"http://localhost:{port}")
                  and (self.headers.get("Content-Type") or "").startswith("application/json")
                  and hmac.compare_digest(self.headers.get("X-MR-Token") or "", token))
            if not ok:
                return self.send(403, {"error": "forbidden"})
            try:
                body = json.loads(raw.decode("utf-8"))
                key, text = body.get("s", ""), body.get("text", "")
                if not known(key):
                    return self.send(404, {"error": "unknown session"})
                text = text if isinstance(text, str) else ""
                if action == "rename":
                    return self.send(200, {"ok": True, "title": hub.rename(key, text)})
                msg = hub.say(key, text)
            except (ValueError, AttributeError) as e:  # bad JSON, empty, too long, session over
                return self.send(400, {"error": str(e)})
            hub.sync(key)
            self.send(200, {"ok": True, "message": msg})

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

    server = Server(("127.0.0.1", port), Handler)
    server.alive = alive
    return server


def window_closed(alive, now=None):
    """A page was connected, none is now, and that has lasted WINDOW_GRACE seconds (a reload reconnects sooner)."""
    now = now or time.time()
    return alive["ever"] and alive["clients"] <= 0 and now - alive["last"] > WINDOW_GRACE


def bind(hub, port=8777, initial=None, replay_first=False):
    """make_server on `port`, or the next free one (something else may hold it)."""
    for attempt in range(PORT_TRIES):
        try:
            return make_server(hub, port + attempt if port else 0, initial, replay_first)
        except OSError:
            if attempt == PORT_TRIES - 1:
                raise
    return None


def serve(hub, port=8777, initial=None, replay_first=False, open_browser=True, hub_file=None):
    """Run the hub until its Edge window is closed (or Ctrl+C). hub_file records the port for `join`."""
    server = bind(hub, port, initial, replay_first)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if hub_file:
        Path(hub_file).parent.mkdir(parents=True, exist_ok=True)
        Path(hub_file).write_text(json.dumps({"port": server.server_address[1], "pid": os.getpid()}),
                                  encoding="utf-8")
    proc = open_window(url) if open_browser else None
    if open_browser and not proc:
        webbrowser.open(url)
    how = "Edge window; closes with it" if proc else "Ctrl+C to stop"
    print(f"CHAT hub: {url}  ({how})", flush=True)
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
        if hub_file:
            try:
                if json.loads(Path(hub_file).read_text(encoding="utf-8")).get("pid") == os.getpid():
                    Path(hub_file).unlink()
            except (OSError, ValueError):
                pass
    print("CHAT closed", flush=True)
    return url
