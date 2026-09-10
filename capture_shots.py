#!/usr/bin/env python3
"""Capture live-site screenshots for Watchtower hub-page slideshows.

Uses Playwright driving the system Chromium at /opt/meta-chromium/chrome.
Network in this sandbox only egresses through the IPv6 egress proxy, which
Chromium cannot reach directly, so a small TCP forwarder must be listening
on 127.0.0.1:3129 -> hatch-egress-proxy:3128 first:

    python3 /tmp/fwd.py &   # or any equivalent 127.0.0.1:3129 relay

Run with the venv python that has playwright installed:
    /tmp/pwenv/bin/python capture_shots.py

Reads targets from data.json (repos with a `pages` URL). Writes
assets/shots/<repo>/shot-{1,2,3}-*.png. Repos without a live site get no
shots; their hub pages render a pure CSS/SVG title card instead.
"""

import json
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data.json")
SHOTS = os.path.join(HERE, "assets", "shots")
CHROME = "/opt/meta-chromium/chrome"
FORWARDER = ("127.0.0.1", 3129)


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
    out = []
    for r in data["repos"]:
        if r.get("pages"):
            out.append((r["name"], r["pages"]))
    return out


def capture(page, url, dest):
    page.goto(url, wait_until="networkidle", timeout=90000)
    page.wait_for_timeout(1200)
    page.screenshot(path=os.path.join(dest, "shot-1-desktop.png"))
    page.evaluate("window.scrollTo(0, Math.max(1200, document.body.scrollHeight * 0.35))")
    page.wait_for_timeout(800)
    page.screenshot(path=os.path.join(dest, "shot-2-desktop.png"))


def main():
    if not forwarder_up():
        sys.exit("capture_shots: no relay on 127.0.0.1:3129 — start the egress forwarder first.")
    if not os.path.exists(CHROME):
        sys.exit("capture_shots: chrome not found at %s" % CHROME)

    from playwright.sync_api import sync_playwright

    todo = targets()
    print("capture_shots: %d targets" % len(todo))
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            proxy={"server": "http://127.0.0.1:3129"},
        )
        for name, url in todo:
            dest = os.path.join(SHOTS, name)
            os.makedirs(dest, exist_ok=True)
            try:
                ctx = browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    ignore_https_errors=True,
                )
                capture(ctx.new_page(), url, dest)
                ctx.close()
                ctx = browser.new_context(
                    viewport={"width": 390, "height": 844},
                    ignore_https_errors=True,
                    is_mobile=True,
                )
                mp = ctx.new_page()
                mp.goto(url, wait_until="networkidle", timeout=90000)
                mp.wait_for_timeout(1200)
                mp.screenshot(path=os.path.join(dest, "shot-3-mobile.png"))
                ctx.close()
                print("  ok %-18s %s" % (name, url))
            except Exception as e:  # noqa: BLE001 - one bad site must not kill the run
                print("  FAIL %-16s %s: %s" % (name, url, str(e).splitlines()[0]))
        browser.close()
    print("done.")


if __name__ == "__main__":
    main()
