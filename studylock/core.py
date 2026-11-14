"""Session state, hosts-file blocking and app killing. Standard library only."""
import json
import math
import os
import subprocess
import time
from pathlib import Path

BEGIN = "# >>> studylock >>>"
END = "# <<< studylock <<<"

DEFAULT_DOMAINS = [
    "instagram.com", "facebook.com", "fb.com", "messenger.com", "twitter.com", "x.com",
    "tiktok.com", "snapchat.com", "reddit.com", "youtube.com", "youtu.be", "twitch.tv",
    "web.telegram.org", "telegram.org", "linkedin.com", "tumblr.com", "threads.net",
    "9gag.com", "netflix.com",
]
# Block DNS-over-HTTPS resolvers so browsers can't bypass /etc/hosts.
DOH_DOMAINS = [
    "dns.google", "cloudflare-dns.com", "mozilla.cloudflare-dns.com",
    "dns.quad9.net", "doh.opendns.com", "dns.nextdns.io",
]
DEFAULT_APPS = ["Telegram", "Messenger", "Instagram", "TikTok", "Snapchat"]


def home_dir() -> Path:
    env = os.environ.get("STUDYLOCK_HOME")
    if env:
        return Path(env)
    return Path("/var/lib/studylock") if os.geteuid() == 0 else Path.home() / ".studylock"


def hosts_path() -> Path:
    return Path(os.environ.get("STUDYLOCK_HOSTS", "/etc/hosts"))


def load_config() -> dict:
    p = home_dir() / "config.json"
    cfg = {"domains": DEFAULT_DOMAINS, "apps": DEFAULT_APPS, "port": 8787, "token": ""}
    if p.exists():
        cfg.update(json.loads(p.read_text()))
    return cfg


def distance_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres (haversine)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def is_near(cfg: dict, lat, lon) -> bool:
    """True if no home is configured, or the phone is within the home radius.

    A missing/invalid phone location counts as 'away' so you are never locked out
    elsewhere by accident.
    """
    home = cfg.get("home")
    if not home:
        return True
    def num(x):  # phones with a comma decimal separator send "46,74165"
        return float(str(x).replace(",", "."))

    try:
        d = distance_m(num(home["lat"]), num(home["lon"]), num(lat), num(lon))
    except (TypeError, ValueError, KeyError):
        return False
    return d <= float(home.get("radius_m", 150))


# ---------- session ----------

class Session:
    def __init__(self, path: Path | None = None):
        self.path = path or home_dir() / "state.json"
        self.data = {"end_at": 0, "tasks": []}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        os.replace(tmp, self.path)

    def start(self, minutes: float, tasks: list[str], now: float | None = None):
        if self.locked(now):
            raise ValueError("a session is already running")
        if minutes <= 0:
            raise ValueError("minutes must be > 0")
        now = now or time.time()
        self.data = {"end_at": now + minutes * 60,
                     "tasks": [{"text": t, "done": False} for t in tasks]}
        self.save()

    def add_task(self, text: str):
        if not self.locked():
            raise ValueError("no active session")
        self.data["tasks"].append({"text": text, "done": False})
        self.save()

    def complete_task(self, index: int):
        self.data["tasks"][index]["done"] = True
        self.save()

    def time_left(self, now: float | None = None) -> float:
        return max(0.0, self.data["end_at"] - (now or time.time()))

    def tasks_left(self) -> int:
        return sum(1 for t in self.data["tasks"] if not t["done"])

    def locked(self, now: float | None = None) -> bool:
        """Locked until the timer has run out AND every task is ticked."""
        if self.data["end_at"] == 0:
            return False
        return self.time_left(now) > 0 or self.tasks_left() > 0

    def status(self, now: float | None = None) -> dict:
        return {"locked": self.locked(now), "seconds_left": int(self.time_left(now)),
                "tasks_left": self.tasks_left(), "tasks": self.data["tasks"]}


# ---------- stay-awake window ----------

def in_window(window, now: float | None = None) -> bool:
    """True if local time is inside a window or any of a list of windows.

    A window is {"start": "HH:MM", "end": "HH:MM"} and may cross midnight.
    """
    if not window:
        return False
    if isinstance(window, list):
        return any(in_window(w, now) for w in window)
    t = time.localtime(now or time.time())
    cur = t.tm_hour * 60 + t.tm_min

    def mins(x):
        h, m = x.split(":")
        return int(h) * 60 + int(m)

    a, b = mins(window["start"]), mins(window["end"])
    return a <= cur < b if a <= b else (cur >= a or cur < b)


_awake_state = {"on": None}


def set_awake(on: bool):
    """Keep the Mac awake with the lid closed (pmset disablesleep) only while `on`."""
    if _awake_state["on"] is on:
        return
    try:
        subprocess.run(["pmset", "-a", "disablesleep", "1" if on else "0"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _awake_state["on"] = on
    except FileNotFoundError:
        pass


# ---------- chat pass ----------

PASS_DEFAULTS = {"wait_s": 60, "minutes": 10, "per_day": 3}


class Pass:
    """Time-boxed 'chat pass' for social apps while at home.

    request() is called twice by the phone: the first call starts the wait,
    a later call after `wait_s` seconds grants the pass (counted per day).
    """

    def __init__(self, cfg: dict | None = None, path: Path | None = None):
        self.cfg = {**PASS_DEFAULTS, **((cfg or {}).get("pass") or {})}
        self.path = path or home_dir() / "passes.json"
        self.d = {"requested_at": 0, "active_until": 0, "day": "", "used": 0}
        if self.path.exists():
            self.d.update(json.loads(self.path.read_text()))

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d))
        os.replace(tmp, self.path)

    def _roll(self, now: float):
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if self.d["day"] != day:
            self.d.update(day=day, used=0)

    def active(self, now: float) -> bool:
        return now < self.d["active_until"]

    def seconds_left(self, now: float) -> int:
        return max(0, int(self.d["active_until"] - now))

    def left_today(self, now: float) -> int:
        self._roll(now)
        return max(0, int(self.cfg["per_day"]) - self.d["used"])

    def request(self, now: float, session_locked: bool, near: bool) -> dict:
        if not near:
            return {"status": "free"}
        if session_locked:
            return {"status": "refused", "reason": "study session running"}
        if self.active(now):
            return {"status": "active", "seconds_left": self.seconds_left(now)}
        self._roll(now)
        if self.left_today(now) <= 0:
            return {"status": "refused", "reason": "no passes left today"}
        wait = float(self.cfg["wait_s"])
        started = self.d["requested_at"]
        if not started or now - started > wait + 300:  # none pending, or it went stale
            self.d["requested_at"] = now
            self._save()
            return {"status": "waiting", "wait_left": int(wait)}
        if now - started < wait:
            return {"status": "waiting", "wait_left": int(wait - (now - started)) + 1}
        self.d.update(requested_at=0, used=self.d["used"] + 1,
                      active_until=now + float(self.cfg["minutes"]) * 60)
        self._save()
        return {"status": "active", "seconds_left": self.seconds_left(now)}

    def blocks_social(self, now: float, session_locked: bool, near: bool) -> bool:
        """At home, social apps are blocked unless a pass is running and no session is."""
        return near and (session_locked or not self.active(now))


# ---------- enforcement ----------

def render_block(domains: list[str]) -> str:
    lines = [BEGIN]
    for d in sorted(set(domains + DOH_DOMAINS)):
        for host in (d, "www." + d) if not d.startswith("www.") else (d,):
            lines.append(f"0.0.0.0 {host}")
            lines.append(f"::1 {host}")
    lines.append(END)
    return "\n".join(lines) + "\n"


def strip_block(text: str) -> str:
    out, skipping = [], False
    for line in text.splitlines():
        if line.strip() == BEGIN:
            skipping = True
        elif line.strip() == END:
            skipping = False
        elif not skipping:
            out.append(line)
    return "\n".join(out).rstrip("\n") + "\n"


def apply_hosts(locked: bool, domains: list[str], path: Path | None = None) -> bool:
    """Make the hosts file match the desired state. Returns True if it changed."""
    path = path or hosts_path()
    current = path.read_text()
    desired = strip_block(current)
    if locked:
        desired += render_block(domains)
    if desired == current:
        return False
    path.write_text(desired)
    for cmd in (["dscacheutil", "-flushcache"], ["killall", "-HUP", "mDNSResponder"]):
        try:  # flush DNS caches so the change takes effect immediately (macOS)
            subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass
    return True


def kill_apps(apps: list[str]):
    for app in apps:
        subprocess.run(["pkill", "-x", app], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
