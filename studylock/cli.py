import argparse
import json
import os
import secrets
import sys
import urllib.request
from pathlib import Path

from .core import home_dir, load_config

PLIST = "/Library/LaunchDaemons/com.studylock.daemon.plist"


def call(path, body=None):
    cfg = load_config()
    url = f"http://127.0.0.1:{cfg['port']}{path}"
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit("error: " + json.load(e).get("error", str(e)))
    except OSError:
        sys.exit("studylock daemon isn't running (run: sudo python3 -m studylock install)")


def show(s):
    if not s["locked"]:
        print("Unlocked. Start a session with: studylock start --minutes 120 -t 'Chapter 3'")
        return
    m, sec = divmod(s["seconds_left"], 60)
    print(f"LOCKED. Timer: {m}m{sec:02d}s left. Tasks left: {s['tasks_left']}")
    for i, t in enumerate(s["tasks"]):
        print(f"  [{'x' if t['done'] else ' '}] {i}: {t['text']}")


def install():
    if os.geteuid() != 0:
        sys.exit("run with sudo: sudo python3 -m studylock install")
    home = home_dir()
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config.json"
    if not cfg.exists():
        cfg.write_text(json.dumps({"token": secrets.token_urlsafe(16)}, indent=2))
    pkg_root = Path(__file__).resolve().parent.parent
    Path(PLIST).write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.studylock.daemon</string>
<key>ProgramArguments</key><array><string>{sys.executable}</string><string>-m</string><string>studylock</string><string>daemon</string></array>
<key>WorkingDirectory</key><string>{pkg_root}</string>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
""")
    os.system(f"launchctl bootout system {PLIST} 2>/dev/null; launchctl bootstrap system {PLIST}")
    print(f"Installed. iPhone token: {load_config()['token']}")


def main():
    p = argparse.ArgumentParser(prog="studylock")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("--minutes", type=float, required=True)
    s.add_argument("-t", "--task", action="append", default=[])
    sub.add_parser("status")
    d = sub.add_parser("done"); d.add_argument("index", type=int)
    a = sub.add_parser("add"); a.add_argument("text")
    sub.add_parser("daemon"); sub.add_parser("install")
    args = p.parse_args()

    if args.cmd == "daemon":
        from .daemon import run
        run()
    elif args.cmd == "install":
        install()
    elif args.cmd == "start":
        show(call("/start", {"minutes": args.minutes, "tasks": args.task}))
    elif args.cmd == "done":
        show(call("/done", {"index": args.index}))
    elif args.cmd == "add":
        show(call("/add", {"text": args.text}))
    else:
        show(call("/status"))
