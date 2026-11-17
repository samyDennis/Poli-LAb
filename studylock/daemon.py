"""Root daemon: enforces the lock and exposes a small HTTP API.

Mutations (start / done / add) are only accepted from localhost; /status is also
readable from your LAN with a token so an iPhone Shortcut can follow the session.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .core import (Pass, Session, apply_hosts, home_dir, in_window, is_near, kill_apps,
                   load_config, set_awake)

LOCK = threading.Lock()


def make_handler(cfg):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _local(self):
            return self.client_address[0] in ("127.0.0.1", "::1")

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            token = q.get("token", [""])[0]
            if u.path not in ("/status", "/pass"):
                return self._send(404, {"error": "not found"})
            expected = load_config()["token"]  # fresh read so token edits apply live
            if not self._local() and (not expected or token != expected):
                return self._send(403, {"error": "bad token"})
            conf = load_config()
            near = is_near(conf, q.get("lat", [None])[0], q.get("lon", [None])[0])
            now = time.time()
            try:  # debugging aid: remember exactly what the phone last sent
                (home_dir() / "last_request.json").write_text(json.dumps({
                    "path": u.path, "lat": q.get("lat"), "lon": q.get("lon"),
                    "near": near, "from": self.client_address[0],
                    "at": time.strftime("%H:%M:%S", time.localtime(now))}))
            except OSError:
                pass
            with LOCK:
                st = Session().status()
                chat = Pass(conf)
                # No block_windows configured = blocked at home all day (old behaviour).
                bw = conf.get("block_windows")
                in_bw = in_window(bw, now) if bw is not None else True
                if u.path == "/pass":
                    return self._send(200, chat.request(now, st["locked"], near, in_bw))
                st["near"] = near
                # Focus: only while a session runs AND the phone is at home.
                st["lock_phone"] = st["locked"] and near
                # Instagram/TikTok/YouTube: blocked at home unless a chat pass is running.
                st["block_social"] = chat.blocks_social(now, st["locked"], near, in_bw)
                st["in_block_window"] = in_bw
                st["pass_seconds_left"] = chat.seconds_left(now) if chat.active(now) else 0
                st["passes_left_today"] = chat.left_today(now)
            self._send(200, st)

        def do_POST(self):
            if not self._local():
                return self._send(403, {"error": "localhost only"})
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            try:
                with LOCK:
                    s = Session()
                    if self.path == "/start":
                        s.start(float(body["minutes"]), body.get("tasks", []))
                    elif self.path == "/add":
                        s.add_task(body["text"])
                    elif self.path == "/done":
                        s.complete_task(int(body["index"]))
                    else:
                        return self._send(404, {"error": "not found"})
                    enforce(s, cfg)
                    self._send(200, s.status())
            except (ValueError, KeyError, IndexError) as e:
                self._send(400, {"error": str(e)})

    return H


def enforce(session: Session, cfg: dict):
    # Optional: stay awake with the lid closed only during these windows
    # (defaults to the block windows, so the phone check works exactly when it matters).
    windows = cfg.get("awake_window", cfg.get("block_windows"))
    if windows is not None:
        set_awake(in_window(windows))
    locked = session.locked()
    apply_hosts(locked, cfg["domains"])
    if locked:
        kill_apps(cfg["apps"])


def run():
    cfg = load_config()
    srv = ThreadingHTTPServer(("0.0.0.0", cfg["port"]), make_handler(cfg))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    while True:  # re-apply every few seconds so manual edits to /etc/hosts get undone
        with LOCK:
            enforce(Session(), load_config())  # re-read config so edits apply without a restart
        time.sleep(3)
