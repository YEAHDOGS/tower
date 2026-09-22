#!/usr/bin/env python3
"""Tower portfolio data builder.

Fetches public repo metadata for the YEAHDOGS org and bakes it into data.json.
Only safe fields are kept (see SAFE_FIELDS below) — no owners, no emails,
no personal data of any kind. Never writes or embeds credentials.

Usage:
    GITHUB_TOKEN=<token> python3 build.py

Optional PII guard:
    PII_BLOCKLIST="token1,token2" GITHUB_TOKEN=<token> python3 build.py
    Aborts without writing if any blocklisted string appears in the output.

Re-run any time to refresh; the 20-minute loop runs it, commits data.json,
and pushes the gh-pages branch.
"""

import glob
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

ORG = "YEAHDOGS"
API = "https://api.github.com"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data.json")

# SHA-256 digests of blocklisted personal-name tokens. The plaintext tokens
# are NEVER stored in source or in any repo — they live only in the
# operator's PII_BLOCKLIST env var. Do not attempt to reverse these.
_PII_DIGESTS = frozenset({
    "9655c2c7cdd9fca965ede488f6872419c249f3eb472e598fff294b8024fa548c",
    "daa0d545b81a4dd98b2db9d70fce671c694376a8c9d1f8cf4750d5a9611684c7",
    "5e93a92dad54c997eb529df41c1f686e5cb2cffdf15c8fad6cbd3a66d7caac25",
    "870e94b1c543092c3894b88587267a2421697bee690ae75b9bb02d5622e8e4ab",
    "fdb7d5c701a3b4a9981e98fd486d22b51b51f2e91605540e57081d440573c009",
    "27044a5ee7023157d3c992b1a8749432d56863bc093b16cc90b736f4b1956b9c",
})

# Progress timelines: per-project dated entries, read from progress/*.json.
# See progress/README.md for the worker contract. Entries are public data.
PROGRESS_DIR = os.path.join(HERE, "progress")
PROGRESS_ENTRY_FIELDS = {
    "date", "title", "kind", "url", "thumb", "detail", "ref", "branch",
}
PROGRESS_KINDS = {"screenshot", "video", "doc", "note", "deploy", "commit"}
PROGRESS_MAX = 40  # newest entries kept per project


def load_progress():
    """progress/<repo>.json -> {repo: [entries, newest first]}. Never raises.

    Malformed entries are dropped with a warning; one bad file never kills
    the build.
    """
    out = {}
    if not os.path.isdir(PROGRESS_DIR):
        return out
    for fn in sorted(os.listdir(PROGRESS_DIR)):
        if not fn.endswith(".json"):
            continue
        repo = fn[: -len(".json")]
        path = os.path.join(PROGRESS_DIR, fn)
        try:
            with open(path) as f:
                raw = json.load(f)
        except Exception as e:  # noqa: BLE001 - bad file, skip it
            print("WARN: progress %s unreadable: %s" % (fn, e), file=sys.stderr)
            continue
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            print("WARN: progress %s has no entries list" % fn, file=sys.stderr)
            continue
        clean = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            if not e.get("date") or not e.get("title") or e.get("kind") not in PROGRESS_KINDS:
                print(
                    "WARN: progress %s: dropping entry (needs date/title/valid kind)" % fn,
                    file=sys.stderr,
                )
                continue
            ce = {k: e[k] for k in PROGRESS_ENTRY_FIELDS if k in e}
            try:
                ts = datetime.fromisoformat(
                    str(ce["date"]).replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, TypeError):
                print(
                    "WARN: progress %s: dropping entry with bad date %r" % (fn, e.get("date")),
                    file=sys.stderr,
                )
                continue
            ce["_ts"] = ts
            clean.append(ce)
        clean.sort(key=lambda e: e["_ts"], reverse=True)
        for e in clean:
            e.pop("_ts", None)
        out[repo] = clean[:PROGRESS_MAX]
    return out


# Allowlist: the ONLY repo-level fields that may appear in data.json.
SAFE_FIELDS = {
    "name", "description", "language", "stargazers_count",
    "pushed_at", "open_issues_count", "html_url", "default_branch",
}


def get_token():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        sys.exit("GITHUB_TOKEN is not set; refusing to run without auth.")
    return token


def api(path):
    """GET path; returns (parsed_json, link_header) or (None, None) on 404."""
    req = urllib.request.Request(
        API + path,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Accept": "application/vnd.github+json",
            "User-Agent": "dogs-dashboard-builder",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp), resp.headers.get("Link")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise


def count_from(link, items):
    """Total result count from a per_page=1 listing via the Link header."""
    if link:
        m = re.search(r'[?&]page=(\d+)[^>]*>;\s*rel="last"', link)
        if m:
            return int(m.group(1))
    if items is None:
        return 0
    return len(items)


def latest_workflow(repo):
    data, _ = api("/repos/%s/%s/actions/runs?per_page=1" % (ORG, repo))
    if not data or not data.get("workflow_runs"):
        return None
    run = data["workflow_runs"][0]
    return {
        "name": run.get("name"),
        "conclusion": run.get("conclusion"),
        "status": run.get("status"),
        "url": run.get("html_url"),
        "updated_at": run.get("updated_at"),
    }


def pages_url(repo):
    data, _ = api("/repos/%s/%s/pages" % (ORG, repo))
    if not data:
        return None
    return data.get("html_url")


def commit_count(repo, branch):
    try:
        items, link = api(
            "/repos/%s/%s/commits?per_page=1&sha=%s" % (ORG, repo, branch)
        )
    except urllib.error.HTTPError as e:
        # Empty repo: GitHub 409s the commits endpoint ("Git Repository is
        # empty"). Zero commits is the honest count, not a skip.
        if e.code == 409:
            return 0
        raise
    return count_from(link, items)


def open_pr_count(repo):
    items, link = api("/repos/%s/%s/pulls?state=open&per_page=1" % (ORG, repo))
    return count_from(link, items)


# Repo renames: asset/dossier slugs that predate a rename, so key art, the
# hub page, and the progress timeline stay wired to the renamed repo.
# (YEAHDOGS/dog -> YEAHDOGS/.dog was consolidated 2026-09-13: assets moved to
# assets/gen/.dog/, progress to progress/.dog.json, dossier to projects/.dog/.)
ASSET_SLUG = {}


def gen_art(name):
    """Generated key art for a repo tile/hub, or None.

    Looks for assets/gen/<name>/hero.* (written by the media pipeline).
    Purely local: no network, no PII surface."""
    hits = sorted(glob.glob(os.path.join(HERE, "assets", "gen", name, "hero.*")))
    if not hits:
        return None
    return "assets/gen/%s/%s" % (name, os.path.basename(hits[0]))


def build_repo(raw):
    name = raw["name"]
    slug = ASSET_SLUG.get(name, name)
    branch = raw.get("default_branch") or "main"
    prs = open_pr_count(name)
    issues_including_prs = raw.get("open_issues_count") or 0
    hub_path = os.path.join("projects", slug, "index.html")
    return {
        "name": name,
        "description": raw.get("description") or "",
        "language": raw.get("language"),
        "stars": raw.get("stargazers_count") or 0,
        "pushed_at": raw.get("pushed_at"),
        "open_issues": max(0, issues_including_prs - prs),
        "open_prs": prs,
        "commits": commit_count(name, branch),
        "url": raw.get("html_url"),
        "branch": branch,
        "workflow": latest_workflow(name),
        "pages": pages_url(name),
        # tile links open the project hub page (projects/<slug>/) when one
        # exists; the redesign's index.html reads r.hub for this.
        "hub": ("projects/" + slug + "/") if os.path.isfile(hub_path) else None,
        # Generated key art (assets/gen/<slug>/hero.*); the tile falls back
        # to this when no live-site screenshot progress entry exists.
        "art": gen_art(slug),
    }


def pii_guard(payload):
    # Anonymity guard: the founder's personal name must never appear on the
    # public site. The blocklist is stored as SHA-256 digests so the
    # plaintext tokens NEVER appear in source. Extra plaintext tokens can be
    # supplied at runtime via the PII_BLOCKLIST env var (comma-separated);
    # they are hashed in memory and never written anywhere.
    blocklist_digests = set(_PII_DIGESTS)
    for tok in os.environ.get("PII_BLOCKLIST", "").split(","):
        tok = tok.strip().lower()
        if tok:
            blocklist_digests.add(hashlib.sha256(tok.encode()).hexdigest())
    if not blocklist_digests:
        return
    blob = json.dumps(payload).lower()
    # Also scan the hand-maintained page shell, not just generated data.
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")) as f:
            blob += "\n" + f.read().lower()
    except OSError:
        pass
    words = re.findall(r"[a-z0-9]+", blob)
    for n in (1, 2, 3):
        for i in range(len(words) - n + 1):
            cand = " ".join(words[i:i + n])
            if hashlib.sha256(cand.encode()).hexdigest() in blocklist_digests:
                sys.exit("PII_GUARD: blocklisted personal-name token in output; aborting.")


def main():
    repos, _ = api("/orgs/%s/repos?per_page=100&type=all" % ORG)
    if not repos:
        sys.exit("No repos returned; aborting.")

    progress_by_repo = load_progress()

    out = []
    for raw in repos:
        # Public site: never list private repos. The org keeps most work
        # private; only publishable repos appear here.
        if raw.get("private"):
            continue
        # Guard clause: only keep allowlisted fields from the raw payload.
        if not all(k in raw for k in ("name", "html_url")):
            continue
        try:
            repo = build_repo(raw)
            # Dated progress timeline from progress/<repo>.json (may be empty).
            # Follows ASSET_SLUG renames so a renamed repo keeps its timeline.
            repo["progress"] = progress_by_repo.get(
                ASSET_SLUG.get(repo["name"], repo["name"]), []
            )
            out.append(repo)
        except Exception as e:  # noqa: BLE001 - one bad repo must not kill the build
            print("WARN: skipping %s: %s" % (raw.get("name"), e), file=sys.stderr)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "org": ORG,
        "repos": out,
    }
    pii_guard(payload)

    with open(OUT, "w") as f:
        json.dump(payload, f, indent=1)
        f.write("\n")
    print("wrote %s (%d repos)" % (OUT, len(out)))


TOKEN = get_token()

if __name__ == "__main__":
    main()
