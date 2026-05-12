#!/usr/bin/env python3
"""
screenlog - takes screenshots at random intervals and publishes to GitHub Pages.
Runs as a launchd daemon. Takes 5-7 screenshots per hour.
"""

import os
import sys
import json
import random
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "pillow"], check=True)
    from PIL import Image, ImageDraw, ImageFont

REPO_DIR = Path(__file__).parent
DOCS_DIR = REPO_DIR / "docs"
SCREENSHOTS_DIR = DOCS_DIR / "screenshots"

# At least 5/hour: 60min / 11min = 5.45, 60min / 8min = 7.5
MIN_INTERVAL_SEC = 8 * 60
MAX_INTERVAL_SEC = 11 * 60

MAX_AGE_HOURS = 48
MAX_WIDTH = 1920
JPEG_QUALITY = 72

LOG_FILE = REPO_DIR / "daemon.log"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def take_screenshot() -> tuple[Path, datetime] | None:
    now = datetime.now()
    stem = now.strftime("%Y%m%d_%H%M%S")
    final_path = SCREENSHOTS_DIR / f"{stem}.jpg"

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            ["screencapture", "-x", "-t", "png", str(tmp_path)],
            capture_output=True, timeout=15
        )
        if result.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size == 0:
            log(f"screencapture failed (rc={result.returncode}): {result.stderr.decode().strip()}")
            return None

        img = Image.open(tmp_path).convert("RGB")

        # Resize if wider than MAX_WIDTH
        if img.width > MAX_WIDTH:
            ratio = MAX_WIDTH / img.width
            img = img.resize((MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)

        # Timestamp badge
        ts_text = now.strftime("%Y-%m-%d  %H:%M:%S")
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        margin, pad = 12, 6
        bbox = draw.textbbox((0, 0), ts_text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        bx = margin
        by = img.height - th - pad * 2 - margin
        draw.rectangle([bx - pad, by - pad, bx + tw + pad, by + th + pad], fill=(0, 0, 0, 180))
        draw.text((bx, by), ts_text, font=font, fill=(220, 220, 220))

        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        img.save(str(final_path), "JPEG", quality=JPEG_QUALITY, optimize=True)
        log(f"Screenshot saved: {final_path.name} ({final_path.stat().st_size // 1024}KB)")
        return final_path, now

    except Exception as e:
        log(f"Screenshot error: {e}")
        return None
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def cleanup_old():
    cutoff = time.time() - MAX_AGE_HOURS * 3600
    removed = 0
    for f in SCREENSHOTS_DIR.glob("*.jpg"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    if removed:
        log(f"Cleaned up {removed} old screenshots")


def build_index() -> list[dict]:
    entries = []
    for f in sorted(SCREENSHOTS_DIR.glob("*.jpg"), reverse=True):
        try:
            dt = datetime.strptime(f.stem, "%Y%m%d_%H%M%S")
            entries.append({
                "file": f.name,
                "iso": dt.isoformat(),
                "display": dt.strftime("%b %-d, %Y  %-I:%M:%S %p"),
                "date": dt.strftime("%A, %B %-d"),
            })
        except ValueError:
            pass
    with open(DOCS_DIR / "index.json", "w") as fh:
        json.dump(entries, fh)
    return entries


def git_push():
    try:
        subprocess.run(["git", "-C", str(REPO_DIR), "add", "-A"], check=True, capture_output=True)
        count_result = subprocess.run(
            ["git", "-C", str(REPO_DIR), "diff", "--cached", "--name-only"],
            capture_output=True, text=True
        )
        if not count_result.stdout.strip():
            log("Nothing to commit")
            return
        msg = f"screenshot {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(
            ["git", "-C", str(REPO_DIR), "commit", "-m", msg],
            check=True, capture_output=True
        )
        result = subprocess.run(
            ["git", "-C", str(REPO_DIR), "push"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            log("Pushed to GitHub")
        else:
            log(f"Push failed: {result.stderr.strip()}")
    except subprocess.CalledProcessError as e:
        log(f"Git error: {e}")


def main():
    log("=== screenlog daemon starting ===")
    log(f"Intervals: {MIN_INTERVAL_SEC//60}-{MAX_INTERVAL_SEC//60} min  |  Max age: {MAX_AGE_HOURS}h")
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    while True:
        result = take_screenshot()
        if result:
            cleanup_old()
            build_index()
            git_push()
        interval = random.randint(MIN_INTERVAL_SEC, MAX_INTERVAL_SEC)
        log(f"Next in {interval // 60}m {interval % 60}s")
        time.sleep(interval)


if __name__ == "__main__":
    main()
