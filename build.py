#!/usr/bin/env python3
"""DOGS mission-control dashboard builder.

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
    items, link = api(
        "/repos/%s/%s/commits?per_page=1&sha=%s" % (ORG, repo, branch)
    )
    return count_from(link, items)


def open_pr_count(repo):
    items, link = api("/repos/%s/%s/pulls?state=open&per_page=1" % (ORG, repo))
    return count_from(link, items)


def build_repo(raw):
    name = raw["name"]
    branch = raw.get("default_branch") or "main"
    prs = open_pr_count(name)
    issues_including_prs = raw.get("open_issues_count") or 0
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
    }


def pii_guard(payload):
    blocklist = [t for t in os.environ.get("PII_BLOCKLIST", "").split(",") if t.strip()]
    if not blocklist:
        return
    blob = json.dumps(payload).lower()
    for token in blocklist:
        if token.strip().lower() in blob:
            sys.exit("PII_GUARD: blocklisted string found in output; aborting.")


def main():
    repos, _ = api("/orgs/%s/repos?per_page=100&type=all" % ORG)
    if not repos:
        sys.exit("No repos returned; aborting.")

    out = []
    for raw in repos:
        # Guard clause: only keep allowlisted fields from the raw payload.
        if not all(k in raw for k in ("name", "html_url")):
            continue
        try:
            out.append(build_repo(raw))
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
