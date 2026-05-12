#!/usr/bin/env python3
"""
screenlog daemon
- Checks active browser URL every ~30s
- Captures screenshot only when browsing social media
- Scrubs visible sensitive patterns (emails, card numbers, SSNs, phone numbers)
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

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "pillow"], check=True)
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ── paths ──────────────────────────────────────────────────────────────────
REPO_DIR = Path(__file__).parent
DOCS_DIR = REPO_DIR / "docs"
SHOTS_DIR = DOCS_DIR / "screenshots"
LOG_FILE  = REPO_DIR / "daemon.log"

# ── timing ─────────────────────────────────────────────────────────────────
POLL_INTERVAL   = 30          # seconds between "am I on social media?" checks
MIN_SHOT_GAP    = 8 * 60      # minimum gap between shots (8 min → max ~7/hr)
MAX_SHOT_GAP    = 11 * 60     # maximum gap (11 min → min ~5/hr guaranteed)
MAX_AGE_HOURS   = 48

# ── image settings ─────────────────────────────────────────────────────────
MAX_WIDTH    = 1920
JPEG_QUALITY = 72
# Blur the browser chrome (address bar). Approximate top fraction of image.
ADDRESSBAR_FRACTION = 0.07    # blur top 7% (address bar area)

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

# ── sensitive text patterns (for OCR-less detection on URL/title) ──────────
# These are checked against the active window TITLE and URL to SKIP shots.
SENSITIVE_TITLE_KEYWORDS = [
    "bank", "login", "sign in", "password", "credit card", "account",
    "paypal", "venmo", "cashapp", "zelle", "chase", "wells fargo",
    "bank of america", "citi", "amex", "american express",
    "tax", "turbotax", "irs", "social security", "ssn",
    "medical", "health", "patient", "insurance", "claim",
    "private", "confidential", "secure", "verify",
]

# ── regex patterns to redact in screenshots (visual blur boxes) ─────────────
# Used with Vision OCR if available; otherwise skipped gracefully.
SENSITIVE_REGEXES = [
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # email
    r"\b4[0-9]{12}(?:[0-9]{3})?\b",                             # Visa
    r"\b5[1-5][0-9]{14}\b",                                     # MC
    r"\b3[47][0-9]{13}\b",                                      # Amex
    r"\b(?:\d{3})-?(?:\d{2})-?(?:\d{4})\b",                    # SSN
    r"\b(?:\d{3}[-.\s]??\d{3}[-.\s]??\d{4}|\(\d{3}\)\s*\d{3}[-.\s]??\d{4})\b",  # phone
]


CONFIG_FILE = REPO_DIR / "config.json"


# ════════════════════════════════════════════════════════════════════════════

def load_ignored_urls():
    """Read config.json and return the list of ignored URL substrings."""
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        return [s.lower() for s in data.get("ignored_urls", [])]
    except Exception:
        return []


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    # Write directly to log file (avoids double-write when launchd captures stdout)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_active_browser_url():
    """Return the URL of the frontmost browser tab, or None."""
    # Try Chrome first, then Safari, then Firefox
    scripts = {
        "Chrome": 'tell application "Google Chrome" to get URL of active tab of front window',
        "Safari": 'tell application "Safari" to get URL of current tab of front window',
        "Firefox": 'tell application "Firefox" to get URL of active tab of front window',
        "Edge": 'tell application "Microsoft Edge" to get URL of active tab of front window',
    }
    for browser, script in scripts.items():
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3
            )
            url = result.stdout.strip()
            if url and url.startswith("http"):
                return url
        except Exception:
            pass
    return None


def is_social_media(url: str) -> bool:
    """Return True if the URL is on a known social media domain."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().lstrip("www.")
        # Check exact match and parent domain
        return host in SOCIAL_DOMAINS or any(
            host.endswith("." + d) or host == d for d in SOCIAL_DOMAINS
        )
    except Exception:
        return False


def is_ignored(url: str, ignored: list) -> bool:
    """Return True if the URL matches any pattern in the ignore list."""
    url_lower = url.lower()
    return any(pattern in url_lower for pattern in ignored)


def title_looks_sensitive(url: str) -> bool:
    """Heuristic: skip if the URL contains sensitive banking/auth keywords."""
    url_lower = url.lower()
    return any(kw in url_lower for kw in SENSITIVE_TITLE_KEYWORDS)


def blur_addressbar(img: Image.Image) -> Image.Image:
    """Blur the top strip of the image where the address bar lives."""
    h = int(img.height * ADDRESSBAR_FRACTION)
    if h < 1:
        return img
    strip = img.crop((0, 0, img.width, h))
    blurred = strip.filter(ImageFilter.GaussianBlur(radius=20))
    img.paste(blurred, (0, 0))
    return img


def try_ocr_and_redact(img: Image.Image) -> Image.Image:
    """
    Attempt to OCR the image and blur any bounding boxes that match
    sensitive patterns. Falls back silently if Vision framework unavailable.
    """
    try:
        import Vision
        import Quartz
        import objc
        from Foundation import NSURL, NSData

        # Save to temp PNG for Vision
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        img.save(tmp_path, "PNG")

        url = NSURL.fileURLWithPath_(tmp_path)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast)
        handler.performRequests_error_([request], None)

        observations = request.results()
        if not observations:
            return img

        draw = ImageDraw.Draw(img)
        iw, ih = img.size
        pattern = re.compile("|".join(SENSITIVE_REGEXES))

        for obs in observations:
            text = obs.topCandidates_(1)[0].string()
            if pattern.search(text):
                bb = obs.boundingBox()
                # VNBoundingBox is normalized (0–1), origin at bottom-left
                x = int(bb.origin.x * iw)
                y = int((1 - bb.origin.y - bb.size.height) * ih)
                w = int(bb.size.width * iw)
                h = int(bb.size.height * ih)
                region = img.crop((x, y, x + w, y + h))
                blurred = region.filter(ImageFilter.GaussianBlur(radius=15))
                img.paste(blurred, (x, y))
                log(f"  Redacted text region: '{text[:30]}…'")

        os.unlink(tmp_path)
        return img

    except Exception:
        # Vision framework not available or failed — skip silently
        return img


def take_screenshot(url: str):
    """Capture screen, process image, return saved path."""
    now = datetime.now()
    stem = now.strftime("%Y%m%d_%H%M%S")
    final_path = SHOTS_DIR / f"{stem}.jpg"

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            ["/usr/sbin/screencapture", "-x", "-t", "png", str(tmp_path)],
            capture_output=True, timeout=15
        )
        if result.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size < 100:
            err = result.stderr.decode().strip()
            log(f"screencapture failed (rc={result.returncode}): {err}")
            if "could not create image" in err or "permission" in err.lower():
                log("⚠ Screen Recording permission required.")
                log("  → System Settings > Privacy & Security > Screen Recording > enable Terminal")
            return None

        img = Image.open(tmp_path).convert("RGB")

        # Resize
        if img.width > MAX_WIDTH:
            ratio = MAX_WIDTH / img.width
            img = img.resize((MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)

        # Privacy: blur address bar
        img = blur_addressbar(img)

        # Privacy: OCR + redact sensitive text (graceful fallback)
        img = try_ocr_and_redact(img)

        # Timestamp badge
        ts_text = now.strftime("%Y-%m-%d  %H:%M:%S")
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        margin, pad = 12, 5
        bbox = draw.textbbox((0, 0), ts_text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        bx, by = margin, img.height - th - pad * 2 - margin
        draw.rectangle([bx - pad, by - pad, bx + tw + pad, by + th + pad], fill=(0, 0, 0, 180))
        draw.text((bx, by), ts_text, font=font, fill=(220, 220, 220))

        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        img.save(str(final_path), "JPEG", quality=JPEG_QUALITY, optimize=True)
        log(f"Saved: {final_path.name}  ({final_path.stat().st_size // 1024}KB)  [{url[:60]}]")
        return final_path

    except Exception as e:
        log(f"Image processing error: {e}")
        return None
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def cleanup_old():
    cutoff = time.time() - MAX_AGE_HOURS * 3600
    removed = [f for f in SHOTS_DIR.glob("*.jpg") if f.stat().st_mtime < cutoff]
    for f in removed:
        f.unlink()
    if removed:
        log(f"Removed {len(removed)} screenshots older than {MAX_AGE_HOURS}h")


def build_index():
    entries = []
    for f in sorted(SHOTS_DIR.glob("*.jpg"), reverse=True):
        try:
            dt = datetime.strptime(f.stem, "%Y%m%d_%H%M%S")
            entries.append({
                "file": f.name,
                "iso":  dt.isoformat(),
                "display": dt.strftime("%b %-d, %Y  %-I:%M:%S %p"),
                "date": dt.strftime("%A, %B %-d"),
            })
        except ValueError:
            pass
    with open(DOCS_DIR / "index.json", "w") as fh:
        json.dump(entries, fh)


def git_push():
    try:
        subprocess.run(["git", "-C", str(REPO_DIR), "add", "-A"],
                       check=True, capture_output=True)
        diff = subprocess.run(
            ["git", "-C", str(REPO_DIR), "diff", "--cached", "--name-only"],
            capture_output=True, text=True
        )
        if not diff.stdout.strip():
            return
        msg = f"screenshot {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "-C", str(REPO_DIR), "commit", "-m", msg],
                       check=True, capture_output=True)
        result = subprocess.run(["git", "-C", str(REPO_DIR), "push"],
                                capture_output=True, text=True)
        if result.returncode == 0:
            log("Pushed to GitHub Pages")
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

    last_shot_time = 0.0
    next_shot_gap  = random.randint(MIN_SHOT_GAP, MAX_SHOT_GAP)

    while True:
        ignored = load_ignored_urls()
        url = get_active_browser_url()

        if url:
            on_social = is_social_media(url)
            ignored_match = is_ignored(url, ignored)
            sensitive = title_looks_sensitive(url)
            now = time.time()
            time_since_last = now - last_shot_time

            if on_social and not ignored_match and not sensitive and time_since_last >= next_shot_gap:
                log(f"On social media: {url[:80]}")
                result = take_screenshot(url)
                if result:
                    cleanup_old()
                    build_index()
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
            log("No browser URL detected (browser may be closed)")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
