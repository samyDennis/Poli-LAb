# StudyLock

No social media on your Mac, and a signal for your iPhone, until you finish studying.
You're "finished" only when **the timer has run out AND every task is ticked**.

## Mac
Requires macOS + Python 3.10+ (no dependencies).

```bash
sudo python3 -m studylock install      # installs a root launchd daemon, prints your iPhone token
studylock() { python3 -m studylock "$@"; }   # or alias it
studylock start --minutes 120 -t "Read chapter 3" -t "Finish exercises"
studylock done 0          # tick a task
studylock add "Review notes"   # you may add tasks, never remove them
studylock status
```

While locked, the root daemon (re-checks every 3 s):
- writes social-media domains + DoH resolvers into `/etc/hosts` (and undoes any manual edit)
- force-quits Discord, WhatsApp, Telegram, etc.
- accepts no "stop" command. Only the timer + checklist unlock it. Customise the lists in
  `/var/lib/studylock/config.json` (`domains`, `apps`).

**Limits (be honest with yourself):** it's an impulse barrier, not a vault. Someone with your admin password
can `sudo launchctl bootout` the daemon, change the clock, or use another device/hotspot. For a true hard
lock, have a friend set your admin password. Web-based hosts blocking won't catch apps that use hard-coded IPs.

## iPhone
iOS won't let a Mac lock your phone, so the phone *follows* the Mac's session:

`http://<mac-ip>:8787/status?token=<token>` → `{"locked": true, "seconds_left": 5400, "tasks_left": 2, ...}`
(find the Mac IP in System Settings → Wi-Fi → Details; give the Mac a fixed/reserved IP in your router.)

1. **Shortcuts → New Shortcut "Study Check"**: *Get Contents of URL* (the URL above) → *Get Dictionary Value* `locked`
   → *If* `locked` is **true** → *Set Focus* **Study** (On) → *Else* → *Set Focus* Off.
2. **Focus → Study**: Allowed apps = Notes, Anki, Calculator, etc. Turn on *Focus Filters*/hide notification badges and
   set the Home Screen/Lock Screen pages to hide social apps.
3. **Shortcuts → Automation** (run immediately, no confirmation): trigger on **NFC tag** stuck in your room *and* on
   **Arrive** home / **Wi-Fi joined** (home network) / **Time of day** (e.g. every 15 min isn't possible, so use
   several times). Action: *Run Shortcut* "Study Check".
4. **Hard mode:** Settings → Screen Time → *App Limits* on social apps (limit 1 min) with a Screen Time passcode set by
   a friend/parent, so you can't just dismiss it. Add a *Downtime* schedule for your study hours.

Tip: the Mac server answers on your LAN only; nothing leaves your network.

## Development
```bash
STUDYLOCK_HOME=/tmp/sl STUDYLOCK_HOSTS=/tmp/hosts python3 -m studylock daemon
python3 -m unittest discover tests
```
