#!/usr/bin/env python3
"""
Hawker menu bar app.

Run:  python3 ~/.screenlog/menubar.py
Stop old daemon first:
  launchctl unload ~/.screenlog/com.mendelrosenberg.screenlog.plist
"""

import atexit
import base64
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

import rumps

_HERE   = Path(__file__).resolve().parent
_SCRIPT = Path(__file__).resolve()
_HOME   = Path.home()

sys.path.insert(0, str(_HERE))
import daemon as _d

_PLIST_LABEL = "com.mendelrosenberg.screenlog"
_PLIST_PATH  = _HOME / "Library" / "LaunchAgents" / f"{_PLIST_LABEL}.plist"
_PIDFILE     = _HERE / "menubar.pid"

# ── Hawker API ───────────────────────────────────────────────────────────────
# Set HAWKER_API_URL and HAWKER_API_KEY in ~/.screenlog/hawker.env
# or as environment variables.

def _load_hawker_env():
    env_file = _HERE / "hawker.env"
    cfg = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
    cfg.setdefault("HAWKER_API_URL", os.environ.get("HAWKER_API_URL", ""))
    cfg.setdefault("HAWKER_API_KEY", os.environ.get("HAWKER_API_KEY", ""))
    return cfg

def _hawker_upload(stem, jpg_path, domain):
    """POST a screenshot to the Hawker Vercel API. Returns True on success."""
    cfg = _load_hawker_env()
    url  = cfg.get("HAWKER_API_URL", "").rstrip("/")
    key  = cfg.get("HAWKER_API_KEY", "")
    if not url or not key:
        _d.log("Hawker upload skipped — HAWKER_API_URL/KEY not configured")
        return False
    try:
        img_b64 = base64.b64encode(Path(jpg_path).read_bytes()).decode()
        payload = json.dumps({
            "stem":        stem,
            "domain":      domain,
            "imageBase64": img_b64,
        }).encode()
        req = urllib.request.Request(
            url + "/api/upload",
            data=payload,
            headers={"Content-Type": "application/json", "x-api-key": key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
            if body.get("ok"):
                _d.log(f"Uploaded to Hawker: {body.get('url','')[:60]}")
                return True
            _d.log(f"Hawker upload error: {body}")
            return False
    except Exception as exc:
        _d.log(f"Hawker upload failed: {exc}")
        return False


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


def _ns_color(rgb_tuple):
    import AppKit
    r, g, b, a = [c / 255.0 for c in rgb_tuple]
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


def _ns_attr_title(text: str, color_tuple: tuple):
    import AppKit
    import Foundation
    font = (AppKit.NSFont.fontWithName_size_("Menlo-Bold", 13)
            or AppKit.NSFont.menuBarFontOfSize_(0))
    attrs = {
        AppKit.NSForegroundColorAttributeName: _ns_color(color_tuple),
        AppKit.NSFontAttributeName: font,
    }
    return Foundation.NSAttributedString.alloc().initWithString_attributes_(text, attrs)


def _plist_exists() -> bool:
    return _PLIST_PATH.exists()


def _write_plist():
    _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PLIST_PATH.write_text(_PLIST_TEMPLATE.format(
        label=_PLIST_LABEL, python=sys.executable,
        script=_SCRIPT, workdir=_HERE, home=_HOME,
    ))


def _remove_plist():
    subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], capture_output=True)
    _PLIST_PATH.unlink(missing_ok=True)


def _acquire_singleton():
    my_pid = os.getpid()
    subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], capture_output=True)
    if _PIDFILE.exists():
        try:
            old = int(_PIDFILE.read_text().strip())
            if old != my_pid:
                os.kill(old, signal.SIGTERM)
                time.sleep(0.5)
        except Exception:
            pass
    _PIDFILE.write_text(str(my_pid))
    atexit.register(lambda: _PIDFILE.unlink(missing_ok=True))


class HawkerApp(rumps.App):

    _GREEN = (34, 197, 94, 255)
    _RED   = (239, 68, 68, 255)

    def __init__(self):
        super().__init__("Hawker", quit_button=None)

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
            rumps.MenuItem("Quit Hawker",  callback=self.on_quit),
        ]

        self._set_face("(o,o)", self._GREEN)
        self._start_loop()

    def _set_face(self, text: str, color: tuple):
        try:
            self._status_item.button().setAttributedTitle_(_ns_attr_title(text, color))
        except Exception:
            self.title = text

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
                            file_path, stem = result
                            _d.cleanup_old()
                            # Upload to Hawker (Vercel) — falls back gracefully if not configured
                            _hawker_upload(stem, file_path, domain)
                            # Also keep local index/git for legacy fallback
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
            self._set_face("(x,x)", self._RED)
        else:
            self._recording = True
            self.toggle_item.title = "Pause Recording"
            self.status_item.title = "● Recording"
            self._set_face("(o,o)", self._GREEN)
            self._start_loop()

    def on_dashboard(self, _):
        cfg = _load_hawker_env()
        url = cfg.get("HAWKER_API_URL", "").rstrip("/") or "https://hawker.vercel.app"
        subprocess.run(["open", url + "/app.html"])

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
        _PIDFILE.unlink(missing_ok=True)
        self._stop_evt.set()
        rumps.quit_application()


if __name__ == "__main__":
    _acquire_singleton()
    HawkerApp().run()
