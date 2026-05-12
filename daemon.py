#!/usr/bin/env python3
"""
screenlog daemon
- Checks active browser URL every ~30s
- Captures screenshot only when browsing social media
- Tracks visits per domain (new visit each time you arrive at a social site)
- Stores domain metadata per screenshot for tombstone support
- Scrubs visible sensitive patterns via Apple Vision OCR
- Blurs the browser address-bar strip
- Pushes to GitHub Pages (docs/ folder in this repo)
"""

import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "pillow"], check=True)
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ── paths ──────────────────────────────────────────────────────────────────
REPO_DIR  = Path(__file__).parent
DOCS_DIR  = REPO_DIR / "docs"
SHOTS_DIR = DOCS_DIR / "screenshots"
LOG_FILE  = REPO_DIR / "daemon.log"
META_FILE = SHOTS_DIR / "metadata.json"   # {stem: {domain, deleted?, deleted_at?}}
VISITS_FILE = REPO_DIR / "visits.json"    # {domain: {visits, last_visit}}

# ── timing ─────────────────────────────────────────────────────────────────
POLL_INTERVAL = 30
MIN_SHOT_GAP  = 8 * 60
MAX_SHOT_GAP  = 11 * 60
MAX_AGE_HOURS = 48

# ── image settings ─────────────────────────────────────────────────────────
MAX_WIDTH           = 1920
JPEG_QUALITY        = 72
ADDRESSBAR_FRACTION = 0.13   # covers tab strip + address bar

# ── social media domains ────────────────────────────────────────────────────
SOCIAL_DOMAINS = {
    "reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com",
    "x.com", "twitter.com", "www.twitter.com",
    "youtube.com", "www.youtube.com", "youtu.be",
    "instagram.com", "www.instagram.com",
    "facebook.com", "www.facebook.com", "fb.com",
    "tiktok.com", "www.tiktok.com",
    "linkedin.com", "www.linkedin.com",
    "twitch.tv", "www.twitch.tv",
    "discord.com", "www.discord.com",
    "tumblr.com", "www.tumblr.com",
    "pinterest.com", "www.pinterest.com",
    "mastodon.social", "bsky.app", "threads.net",
    "snapchat.com", "www.snapchat.com",
    "vimeo.com", "www.vimeo.com",
    "hackernews.com", "news.ycombinator.com",
}

SENSITIVE_TITLE_KEYWORDS = [
    "bank", "login", "sign in", "password", "credit card", "account",
    "paypal", "venmo", "cashapp", "zelle", "chase", "wells fargo",
    "bank of america", "citi", "amex", "american express",
    "tax", "turbotax", "irs", "social security", "ssn",
    "medical", "health", "patient", "insurance", "claim",
    "private", "confidential", "secure", "verify",
]

SENSITIVE_REGEXES = [
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    r"\b4[0-9]{12}(?:[0-9]{3})?\b",
    r"\b5[1-5][0-9]{14}\b",
    r"\b3[47][0-9]{13}\b",
    r"\b(?:\d{3})-?(?:\d{2})-?(?:\d{4})\b",
    r"\b(?:\d{3}[-.\s]??\d{3}[-.\s]??\d{4}|\(\d{3}\)\s*\d{3}[-.\s]??\d{4})\b",
]

CONFIG_FILE = REPO_DIR / "config.json"


# ════════════════════════════════════════════════════════════════════════════
# Metadata helpers

def load_metadata():
    try:
        return json.loads(META_FILE.read_text())
    except Exception:
        return {}


def save_metadata(data):
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    META_FILE.write_text(json.dumps(data, indent=2))


def load_visits():
    try:
        return json.loads(VISITS_FILE.read_text())
    except Exception:
        return {}


def save_visits(data):
    VISITS_FILE.write_text(json.dumps(data, indent=2))


# ════════════════════════════════════════════════════════════════════════════

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def extract_domain(url):
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return "unknown"


def load_ignored_urls():
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        return [s.lower() for s in data.get("ignored_urls", [])]
    except Exception:
        return []


def get_active_browser_url():
    scripts = {
        "Chrome": 'tell application "Google Chrome" to get URL of active tab of front window',
        "Safari": 'tell application "Safari" to get URL of current tab of front window',
        "Firefox": 'tell application "Firefox" to get URL of active tab of front window',
        "Edge":   'tell application "Microsoft Edge" to get URL of active tab of front window',
    }
    for browser, script in scripts.items():
        try:
            result = subprocess.run(["osascript", "-e", script],
                                    capture_output=True, text=True, timeout=3)
            url = result.stdout.strip()
            if url and url.startswith("http"):
                return url
        except Exception:
            pass
    return None


def is_social_media(url):
    try:
        host = urlparse(url).netloc.lower().lstrip("www.")
        return host in SOCIAL_DOMAINS or any(
            host.endswith("." + d) or host == d for d in SOCIAL_DOMAINS
        )
    except Exception:
        return False


def is_ignored(url, ignored):
    url_lower = url.lower()
    return any(pattern in url_lower for pattern in ignored)


def title_looks_sensitive(url):
    url_lower = url.lower()
    return any(kw in url_lower for kw in SENSITIVE_TITLE_KEYWORDS)


def _today():
    return datetime.now().strftime("%Y-%m-%d")

def _week():
    from datetime import date
    d = date.today()
    return f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"


def record_visit(domain):
    """Increment visit counter and initialise daily/weekly buckets."""
    visits = load_visits()
    entry = visits.get(domain, {
        "total_visits": 0, "total_time_seconds": 0,
        "last_visit": None, "daily": {}, "weekly": {},
    })
    today, week = _today(), _week()
    entry["total_visits"] += 1
    entry["last_visit"] = datetime.now().isoformat()
    entry.setdefault("daily", {})[today] = {
        "visits": entry["daily"].get(today, {}).get("visits", 0) + 1,
        "time_seconds": entry["daily"].get(today, {}).get("time_seconds", 0),
    }
    entry.setdefault("weekly", {})[week] = {
        "visits": entry["weekly"].get(week, {}).get("visits", 0) + 1,
        "time_seconds": entry["weekly"].get(week, {}).get("time_seconds", 0),
    }
    visits[domain] = entry
    save_visits(visits)
    log(f"Visit #{entry['total_visits']} to {domain}")


def add_domain_time(domain, seconds):
    """Add dwell time to a domain's stats."""
    visits = load_visits()
    entry = visits.get(domain, {
        "total_visits": 0, "total_time_seconds": 0,
        "last_visit": None, "daily": {}, "weekly": {},
    })
    today, week = _today(), _week()
    entry["total_time_seconds"] = entry.get("total_time_seconds", 0) + seconds
    day = entry.setdefault("daily", {}).setdefault(today, {"visits": 0, "time_seconds": 0})
    day["time_seconds"] += seconds
    wk = entry.setdefault("weekly", {}).setdefault(week, {"visits": 0, "time_seconds": 0})
    wk["time_seconds"] += seconds
    visits[domain] = entry
    save_visits(visits)


def get_browser_window_id():
    """Return the CGWindow ID of the frontmost browser window, or None."""
    try:
        import Quartz
        BROWSERS = {"Google Chrome", "Safari", "Firefox", "Microsoft Edge", "Opera"}
        opts = (Quartz.kCGWindowListOptionOnScreenOnly |
                Quartz.kCGWindowListExcludeDesktopElements)
        for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID):
            if (any(b in w.get("kCGWindowOwnerName", "") for b in BROWSERS)
                    and w.get("kCGWindowLayer", 999) == 0
                    and w.get("kCGWindowAlpha", 0) > 0):
                return w.get("kCGWindowNumber")
    except Exception:
        pass
    return None


def blur_addressbar(img):
    h = int(img.height * ADDRESSBAR_FRACTION)
    if h < 1:
        return img
    strip = img.crop((0, 0, img.width, h))
    img.paste(strip.filter(ImageFilter.GaussianBlur(radius=20)), (0, 0))
    return img


def try_ocr_and_redact(img):
    try:
        import Vision
        from Foundation import NSURL

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        img.save(tmp_path, "PNG")

        url = NSURL.fileURLWithPath_(tmp_path)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast)
        handler.performRequests_error_([request], None)

        observations = request.results()
        if observations:
            draw = ImageDraw.Draw(img)
            iw, ih = img.size
            pattern = re.compile("|".join(SENSITIVE_REGEXES))
            for obs in observations:
                text = obs.topCandidates_(1)[0].string()
                if pattern.search(text):
                    bb = obs.boundingBox()
                    x = int(bb.origin.x * iw)
                    y = int((1 - bb.origin.y - bb.size.height) * ih)
                    w = int(bb.size.width * iw)
                    h = int(bb.size.height * ih)
                    region = img.crop((x, y, x + w, y + h))
                    img.paste(region.filter(ImageFilter.GaussianBlur(radius=15)), (x, y))
                    log(f"  Redacted: '{text[:30]}…'")
        os.unlink(tmp_path)
    except Exception:
        pass
    return img


def take_screenshot(url, domain):
    now = datetime.now()
    stem = now.strftime("%Y%m%d_%H%M%S")
    final_path = SHOTS_DIR / f"{stem}.jpg"

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        # Prefer window-only capture (excludes desktop, other apps, other windows)
        wid = get_browser_window_id()
        if wid:
            cmd = ["/usr/sbin/screencapture", "-x", "-l", str(wid), "-t", "png", str(tmp_path)]
        else:
            cmd = ["/usr/sbin/screencapture", "-x", "-t", "png", str(tmp_path)]
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size < 100:
            err = result.stderr.decode().strip()
            log(f"screencapture failed (rc={result.returncode}): {err}")
            if "could not create image" in err:
                log("⚠ → System Settings > Privacy & Security > Screen Recording > enable Terminal")
            return None

        img = Image.open(tmp_path).convert("RGB")
        if img.width > MAX_WIDTH:
            ratio = MAX_WIDTH / img.width
            img = img.resize((MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)

        img = blur_addressbar(img)
        img = try_ocr_and_redact(img)

        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        ts_text = now.strftime("%Y-%m-%d  %H:%M:%S")
        margin, pad = 12, 5
        bbox = draw.textbbox((0, 0), ts_text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        bx, by = margin, img.height - th - pad * 2 - margin
        draw.rectangle([bx - pad, by - pad, bx + tw + pad, by + th + pad], fill=(0, 0, 0, 180))
        draw.text((bx, by), ts_text, font=font, fill=(220, 220, 220))

        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        img.save(str(final_path), "JPEG", quality=JPEG_QUALITY, optimize=True)

        # Save domain metadata
        meta = load_metadata()
        meta[stem] = {"domain": domain}
        save_metadata(meta)

        log(f"Saved: {final_path.name}  ({final_path.stat().st_size // 1024}KB)  [{url[:60]}]")
        return final_path, stem

    except Exception as e:
        log(f"Image processing error: {e}")
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def cleanup_old():
    cutoff = time.time() - MAX_AGE_HOURS * 3600
    removed = []
    for f in SHOTS_DIR.glob("*.jpg"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed.append(f.stem)
    if removed:
        log(f"Removed {len(removed)} old screenshots")


def build_index():
    meta = load_metadata()
    entries = []
    stems_with_jpg = set()

    for jpg in sorted(SHOTS_DIR.glob("*.jpg"), reverse=True):
        try:
            dt = datetime.strptime(jpg.stem, "%Y%m%d_%H%M%S")
            stems_with_jpg.add(jpg.stem)
            m = meta.get(jpg.stem, {})
            entries.append({
                "file":    jpg.name,
                "stem":    jpg.stem,
                "iso":     dt.isoformat(),
                "display": dt.strftime("%b %-d, %Y  %-I:%M:%S %p"),
                "date":    dt.strftime("%A, %B %-d"),
                "domain":  m.get("domain"),
                "deleted": False,
            })
        except ValueError:
            pass

    # Tombstones: metadata entries marked deleted with no corresponding jpg
    for stem, m in sorted(meta.items(), reverse=True):
        if m.get("deleted") and stem not in stems_with_jpg:
            try:
                dt = datetime.strptime(stem, "%Y%m%d_%H%M%S")
                entries.append({
                    "file":       None,
                    "stem":       stem,
                    "iso":        dt.isoformat(),
                    "display":    dt.strftime("%b %-d, %Y  %-I:%M:%S %p"),
                    "date":       dt.strftime("%A, %B %-d"),
                    "domain":     m.get("domain"),
                    "deleted":    True,
                    "deleted_at": m.get("deleted_at"),
                })
            except ValueError:
                pass

    entries.sort(key=lambda e: e["iso"], reverse=True)
    with open(DOCS_DIR / "index.json", "w") as fh:
        json.dump(entries, fh)


def build_stats():
    """Write docs/stats.json: public ignore list + visit data for the gallery."""
    try:
        cfg    = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
        visits = load_visits()
        # Top 5 by total visits for public display
        top5 = sorted(visits.items(), key=lambda x: x[1].get("total_visits", 0), reverse=True)[:5]
        stats = {
            "ignored_urls": cfg.get("ignored_urls", []),
            "visits": {d: v for d, v in top5},
        }
        (DOCS_DIR / "stats.json").write_text(json.dumps(stats))
    except Exception as e:
        log(f"build_stats error: {e}")


def git_push(msg=None):
    try:
        subprocess.run(["git", "-C", str(REPO_DIR), "add", "-A"],
                       check=True, capture_output=True)
        diff = subprocess.run(
            ["git", "-C", str(REPO_DIR), "diff", "--cached", "--name-only"],
            capture_output=True, text=True
        )
        if not diff.stdout.strip():
            return
        commit_msg = msg or f"screenshot {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "-C", str(REPO_DIR), "commit", "-m", commit_msg],
                       check=True, capture_output=True)
        result = subprocess.run(["git", "-C", str(REPO_DIR), "push"],
                                capture_output=True, text=True)
        if result.returncode == 0:
            log("Pushed to GitHub Pages ✓")
        else:
            log(f"Push failed: {result.stderr.strip()}")
    except subprocess.CalledProcessError as e:
        log(f"Git error: {e}")


def main():
    log("=" * 60)
    log("screenlog daemon starting")
    log(f"Poll every {POLL_INTERVAL}s | Screenshot gap {MIN_SHOT_GAP//60}-{MAX_SHOT_GAP//60}m")
    log(f"Social media domains: {len(SOCIAL_DOMAINS)} | Max age: {MAX_AGE_HOURS}h")
    log("=" * 60)
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)

    last_shot_time  = 0.0
    next_shot_gap   = random.randint(MIN_SHOT_GAP, MAX_SHOT_GAP)
    last_domain     = None   # for visit counting — fires when domain changes

    while True:
        ignored = load_ignored_urls()
        url     = get_active_browser_url()

        if url:
            on_social     = is_social_media(url)
            ignored_match = is_ignored(url, ignored)
            sensitive     = title_looks_sensitive(url)
            domain        = extract_domain(url) if on_social else None
            now           = time.time()

            # Count a visit when arriving at a new social domain
            if on_social and not ignored_match and not sensitive:
                if domain != last_domain:
                    record_visit(domain)
                last_domain = domain
            else:
                last_domain = None

            # Accumulate dwell time every poll cycle we're on the same domain
            if on_social and not ignored_match and not sensitive and domain == last_domain:
                add_domain_time(domain, POLL_INTERVAL)

            if on_social and not ignored_match and not sensitive \
                    and (now - last_shot_time) >= next_shot_gap:
                log(f"On social media: {url[:80]}")
                result = take_screenshot(url, domain)
                if result:
                    cleanup_old()
                    build_index()
                    build_stats()
                    git_push()
                    last_shot_time = time.time()
                    next_shot_gap  = random.randint(MIN_SHOT_GAP, MAX_SHOT_GAP)
                    log(f"Next shot in {next_shot_gap // 60}m {next_shot_gap % 60}s")
            elif ignored_match:
                log(f"Ignored URL, skipping: {url[:60]}")
            elif not on_social:
                log(f"Not social media, skipping: {url[:60]}")
            elif sensitive:
                log(f"Sensitive URL, skipping: {url[:60]}")
        else:
            last_domain = None
            log("No browser URL detected (browser may be closed)")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
