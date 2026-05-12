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


def _make_icon(color: tuple, name: str):
    """Render the ASCII hawk face in `color` as a PNG. Returns file path or None."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        lines = [r"\(o,o)/", r" \)X(/"]
        font_size = 13

        font = None
        for candidate in [
            "/System/Library/Fonts/Menlo.ttc",
            "/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/Courier New.ttf",
        ]:
            try:
                font = ImageFont.truetype(candidate, font_size)
                break
            except Exception:
                pass
        if font is None:
            font = ImageFont.load_default()

        def _bbox(text):
            probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            try:
                b = probe.textbbox((0, 0), text, font=font)
                return b[2] - b[0], b[3] - b[1]
            except AttributeError:
                return probe.textsize(text, font=font)

        sizes    = [_bbox(l) for l in lines]
        pad      = 2
        line_gap = 1
        W = max(s[0] for s in sizes) + pad * 2
        H = sum(s[1] for s in sizes) + pad * 2 + line_gap * (len(lines) - 1)

        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d   = ImageDraw.Draw(img)

        y = pad
        for line, (_, h) in zip(lines, sizes):
            d.text((pad, y), line, fill=color, font=font)
            y += h + line_gap

        path = Path(tempfile.gettempdir()) / f"loghawk_icon_{name}.png"
        img.save(str(path))
        return str(path)
    except Exception:
        return None


def _plist_exists() -> bool:
    return _PLIST_PATH.exists()


def _write_plist():
    # Just drop the file — ~/Library/LaunchAgents plists are picked up at next login
    # automatically. Don't call launchctl load or RunAtLoad would spawn a second instance.
    _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PLIST_PATH.write_text(_PLIST_TEMPLATE.format(
        label=_PLIST_LABEL, python=sys.executable,
        script=_SCRIPT, workdir=_HERE, home=_HOME,
    ))


def _remove_plist():
    _PLIST_PATH.unlink(missing_ok=True)


class LogHawkApp(rumps.App):

    # green = recording, red = paused
    _GREEN = (34, 197, 94, 255)
    _RED   = (239, 68, 68, 255)

    def __init__(self):
        self._icon_on  = _make_icon(self._GREEN, "on")
        self._icon_off = _make_icon(self._RED,   "off")
        super().__init__(
            "Log Hawk",
            icon=self._icon_on,
            template=False,
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
            if self._icon_off:
                self.icon = self._icon_off
        else:
            self._recording = True
            self.toggle_item.title = "Pause Recording"
            self.status_item.title = "● Recording"
            if self._icon_on:
                self.icon = self._icon_on
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
