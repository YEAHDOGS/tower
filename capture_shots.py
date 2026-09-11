#!/usr/bin/env python3
"""Capture live-site screenshots for Watchtower slideshows — the capture-refresh loop.

Uses Playwright driving the system Chromium at /opt/meta-chromium/chrome.
Network in this sandbox only egresses through the IPv6 egress proxy, so a small
TCP forwarder must be listening on 127.0.0.1:3129 -> hatch-egress-proxy:3128 first:

    python3 /tmp/fwd3129.py &

Playwright (NOT `chrome --screenshot`) is the working path here — headless
chrome flags refused to write screenshots in this sandbox, but the Playwright
route captures fine (~10s per site).

Freshness is tracked in assets/shots/manifest.json:
    { "<repo>": {"url": ..., "captured_at": "<ISO UTC>", "files": [...]}, ... }

Usage (run with a python that has playwright + PIL):
    python3 capture_shots.py                 # refresh stale only (>14 days) or missing
    python3 capture_shots.py --all           # force re-capture every target
    python3 capture_shots.py --only wax      # force re-capture one repo
    python3 capture_shots.py --dry-run       # list what would be captured, do nothing

Writes assets/shots/<repo>/shot-{1,2}-desktop.webp (1440x900) and
shot-3-mobile.webp (390x844). Repos without a live `pages` URL get no shots;
their pages render a pure CSS/SVG title card instead.
"""

import argparse
import datetime
import io
import json
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data.json")
SHOTS = os.path.join(HERE, "assets", "shots")
MANIFEST = os.path.join(SHOTS, "manifest.json")
CHROME = "/opt/meta-chromium/chrome"
FORWARDER = ("127.0.0.1", 3129)
STALE_DAYS = 14
WEBP_Q = 75


def forwarder_up():
    try:
        s = socket.create_connection(FORWARDER, timeout=5)
        s.close()
        return True
    except OSError:
        return False


def targets():
    with open(DATA) as f:
        data = json.load(f)
    return [(r["name"], r["pages"]) for r in data["repos"] if r.get("pages")]


def load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            return json.load(f)
    return {}


def save_manifest(m):
    os.makedirs(SHOTS, exist_ok=True)
    with open(MANIFEST, "w") as f:
        json.dump(m, f, indent=2, sort_keys=True)


def is_stale(name, manifest):
    entry = manifest.get(name)
    if not entry or not entry.get("captured_at"):
        return True, "no manifest entry"
    try:
        ts = datetime.datetime.fromisoformat(entry["captured_at"])
    except ValueError:
        return True, "bad timestamp"
    age = datetime.datetime.now(datetime.timezone.utc) - ts
    if age > datetime.timedelta(days=STALE_DAYS):
        return True, "%d days old" % age.days
    dest = os.path.join(SHOTS, name)
    for fn in ("shot-1-desktop.webp", "shot-2-desktop.webp", "shot-3-mobile.webp"):
        if not os.path.exists(os.path.join(dest, fn)):
            return True, "missing %s" % fn
    return False, "%d days old" % age.days


def save_webp(png_bytes, path):
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    img.save(path, "WEBP", quality=WEBP_Q, method=6)


def capture_all(page, mobile_page, url, dest):
    files = []
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3500)
    save_webp(page.screenshot(), os.path.join(dest, "shot-1-desktop.webp"))
    files.append("shot-1-desktop.webp")
    page.evaluate("window.scrollTo(0, Math.max(1200, document.body.scrollHeight * 0.35))")
    page.wait_for_timeout(1200)
    save_webp(page.screenshot(), os.path.join(dest, "shot-2-desktop.webp"))
    files.append("shot-2-desktop.webp")
    mobile_page.goto(url, wait_until="domcontentloaded", timeout=60000)
    mobile_page.wait_for_timeout(3500)
    save_webp(mobile_page.screenshot(), os.path.join(dest, "shot-3-mobile.webp"))
    files.append("shot-3-mobile.webp")
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="force re-capture every target")
    ap.add_argument("--only", metavar="NAME", help="force re-capture one repo")
    ap.add_argument("--dry-run", action="store_true", help="list what would run, do nothing")
    args = ap.parse_args()

    manifest = load_manifest()
    todo_all = targets()

    if args.only:
        todo = [t for t in todo_all if t[0] == args.only]
        if not todo:
            sys.exit("capture_shots: no target named %r" % args.only)
        reasons = {args.only: "forced (--only)"}
    else:
        todo, reasons = [], {}
        for name, url in todo_all:
            if args.all:
                todo.append((name, url))
                reasons[name] = "forced (--all)"
            else:
                stale, why = is_stale(name, manifest)
                if stale:
                    todo.append((name, url))
                    reasons[name] = why

    if not todo:
        print("capture_shots: all %d targets fresh (<= %d days)" % (len(todo_all), STALE_DAYS))
        return
    print("capture_shots: %d of %d targets to refresh" % (len(todo), len(todo_all)))
    for name, url in todo:
        print("  %-20s %s  [%s]" % (name, url, reasons[name]))
    if args.dry_run:
        return

    if not forwarder_up():
        sys.exit("capture_shots: no relay on 127.0.0.1:3129 — start the egress forwarder first.")
    if not os.path.exists(CHROME):
        sys.exit("capture_shots: chrome not found at %s" % CHROME)

    from playwright.sync_api import sync_playwright

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            proxy={"server": "http://127.0.0.1:3129"},
        )
        dctx = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
        mctx = browser.new_context(
            viewport={"width": 390, "height": 844}, ignore_https_errors=True, is_mobile=True
        )
        dpage, mpage = dctx.new_page(), mctx.new_page()
        for name, url in todo:
            dest = os.path.join(SHOTS, name)
            os.makedirs(dest, exist_ok=True)
            try:
                files = capture_all(dpage, mpage, url, dest)
                manifest[name] = {"url": url, "captured_at": now, "files": files}
                print("  ok %-18s %s" % (name, url))
            except Exception as e:  # noqa: BLE001 - one bad site must not kill the run
                print("  FAIL %-16s %s: %s" % (name, url, str(e).splitlines()[0]))
        browser.close()
    save_manifest(manifest)
    print("done. manifest updated: %s" % MANIFEST)


if __name__ == "__main__":
    main()
