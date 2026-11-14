"""Root daemon: enforces the lock and exposes a small HTTP API.

Mutations (start / done / add) are only accepted from localhost; /status is also
readable from your LAN with a token so an iPhone Shortcut can follow the session.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .core import Session, apply_hosts, kill_apps, load_config

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
            token = parse_qs(u.query).get("token", [""])[0]
            if u.path != "/status":
                return self._send(404, {"error": "not found"})
            if not self._local() and (not cfg["token"] or token != cfg["token"]):
                return self._send(403, {"error": "bad token"})
            with LOCK:
                self._send(200, Session().status())

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
