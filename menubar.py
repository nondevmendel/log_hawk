#!/usr/bin/env python3
"""
Log Hawk menu bar app — replaces the headless launchd daemon.

Run:  python3 ~/.screenlog/menubar.py
Stop old daemon first:
  launchctl unload ~/.screenlog/com.mendelrosenberg.screenlog.plist
"""

import random
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import rumps

_HERE   = Path(__file__).resolve().parent
_SCRIPT = Path(__file__).resolve()
_HOME   = Path.home()

sys.path.insert(0, str(_HERE))
import daemon as _d

_PLIST_LABEL = "com.mendelrosenberg.screenlog"
_PLIST_PATH  = _HOME / "Library" / "LaunchAgents" / f"{_PLIST_LABEL}.plist"

_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{script}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{workdir}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>LimitLoadToSessionType</key>
  <string>Aqua</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin</string>
    <key>HOME</key>
    <string>{home}</string>
  </dict>
</dict>
</plist>
"""


def _make_icon():
    """Hawk head with wings raised, rendered as black-on-transparent PNG.
    Returns temp file path, or None if PIL unavailable."""
    try:
        from PIL import Image, ImageDraw
        px  = 44
        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        d   = ImageDraw.Draw(img)
        c   = (0, 0, 0, 255)

        # Head oval
        d.ellipse([13, 13, 31, 31], outline=c, width=2)
        # Eyes
        d.ellipse([16, 18, 20, 22], fill=c)
        d.ellipse([24, 18, 28, 22], fill=c)
        # Beak
        d.polygon([(22, 25), (19, 29), (25, 29)], fill=c)
        # Crest
        d.line([22, 13, 22, 8],  fill=c, width=2)
        d.line([22,  8, 19, 5],  fill=c, width=2)
        d.line([22,  8, 25, 5],  fill=c, width=2)
        # Left wing raised  (\)
        d.line([13, 19,  3,  8], fill=c, width=2)
        d.line([ 3,  8,  1, 12], fill=c, width=2)
        d.line([ 3,  8,  6,  5], fill=c, width=2)
        # Right wing raised (/)
        d.line([31, 19, 41,  8], fill=c, width=2)
        d.line([41,  8, 38,  5], fill=c, width=2)
        d.line([41,  8, 43, 12], fill=c, width=2)

        path = Path(tempfile.gettempdir()) / "loghawk_icon.png"
        img.save(str(path))
        return str(path)
    except Exception:
        return None


def _plist_exists() -> bool:
    return _PLIST_PATH.exists()


def _write_plist():
    _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PLIST_PATH.write_text(_PLIST_TEMPLATE.format(
        label=_PLIST_LABEL, python=sys.executable,
        script=_SCRIPT, workdir=_HERE, home=_HOME,
    ))
    subprocess.run(["launchctl", "load", str(_PLIST_PATH)], capture_output=True)


def _remove_plist():
    subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], capture_output=True)
    _PLIST_PATH.unlink(missing_ok=True)


class LogHawkApp(rumps.App):

    def __init__(self):
        icon = _make_icon()
        super().__init__(
            "Log Hawk",
            icon=icon,
            template=True if icon else None,
            quit_button=None,
        )

        self._recording = True
        self._stop_evt  = threading.Event()
        self._thread    = None

        self.status_item = rumps.MenuItem("● Recording")
        self.toggle_item = rumps.MenuItem("Pause Recording", callback=self.on_toggle)
        self.login_item  = rumps.MenuItem("Launch at Login",  callback=self.on_login)
        self.login_item.state = _plist_exists()

        self.menu = [
            self.status_item,
            None,
            self.toggle_item,
            None,
            rumps.MenuItem("Open Dashboard", callback=self.on_dashboard),
            rumps.MenuItem("Open Log File",  callback=self.on_logfile),
            None,
            self.login_item,
            None,
            rumps.MenuItem("Quit Log Hawk",  callback=self.on_quit),
        ]

        self._start_loop()

    # ── daemon loop ──────────────────────────────────────────────────────────

    def _start_loop(self):
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        last_shot_time = 0.0
        next_shot_gap  = random.randint(_d.MIN_SHOT_GAP, _d.MAX_SHOT_GAP)
        last_domain    = None

        while not self._stop_evt.is_set():
            try:
                ignored = _d.load_ignored_urls()
                url     = _d.get_active_browser_url()

                if url:
                    on_social     = _d.is_social_media(url)
                    ignored_match = _d.is_ignored(url, ignored)
                    sensitive     = _d.title_looks_sensitive(url)
                    domain        = _d.extract_domain(url) if on_social else None
                    now           = time.time()

                    if on_social and not ignored_match and not sensitive:
                        if domain != last_domain:
                            _d.record_visit(domain)
                        last_domain = domain
                    else:
                        last_domain = None

                    if (on_social and not ignored_match and not sensitive
                            and domain == last_domain):
                        _d.add_domain_time(domain, _d.POLL_INTERVAL)

                    if (on_social and not ignored_match and not sensitive
                            and (now - last_shot_time) >= next_shot_gap):
                        _d.log(f"On social media: {url[:80]}")
                        result = _d.take_screenshot(url, domain)
                        if result:
                            _d.cleanup_old()
                            _d.build_index()
                            _d.build_stats()
                            _d.git_push()
                            last_shot_time = time.time()
                            next_shot_gap  = random.randint(_d.MIN_SHOT_GAP, _d.MAX_SHOT_GAP)
                            _d.log(f"Next shot in {next_shot_gap // 60}m {next_shot_gap % 60}s")
                else:
                    last_domain = None

            except Exception as exc:
                _d.log(f"menubar loop error: {exc}")

            self._stop_evt.wait(_d.POLL_INTERVAL)

    # ── menu callbacks ───────────────────────────────────────────────────────

    def on_toggle(self, _):
        if self._recording:
            self._recording = False
            self._stop_evt.set()
            self.toggle_item.title = "Resume Recording"
            self.status_item.title = "○ Paused"
        else:
            self._recording = True
            self.toggle_item.title = "Pause Recording"
            self.status_item.title = "● Recording"
            self._start_loop()

    def on_dashboard(self, _):
        subprocess.run(["open", "https://nondevmendel.github.io/log_hawk/"])

    def on_logfile(self, _):
        subprocess.run(["open", str(_d.LOG_FILE)])

    def on_login(self, _):
        if _plist_exists():
            _remove_plist()
            self.login_item.state = 0
        else:
            _write_plist()
            self.login_item.state = 1

    def on_quit(self, _):
        self._stop_evt.set()
        rumps.quit_application()


if __name__ == "__main__":
    LogHawkApp().run()
