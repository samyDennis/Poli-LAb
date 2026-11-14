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
    try:
        d = distance_m(float(home["lat"]), float(home["lon"]), float(lat), float(lon))
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
