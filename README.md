# Watchtower — project portfolio + status board

Public, zero-PII portfolio of every YEAHDOGS project. Live at
https://yeahdogs.github.io/tower/

## What it shows

A Metro-style tile grid: one square tile per repo with its name, one-line
description, category, live-site + repo links, and a live site-status dot
fed from the Watchtower engine's status.json feed (baked into data.json at
build time — never a fake green). Search filters tiles by name/description;
sort offers featured, name, status, recency, and category; category chips
filter Apps / Trading / Infra / Docs.

Header carries the DOGS identity badge ("This product was made by DOGS",
linking to wearedogs.net); semantic HTML (`<article>` tiles with real links), proper
`<title>`, meta description, and Open Graph tags make it indexable.

Data is baked into `data.json` at build time from public repo metadata.
No personal data is collected, stored, or displayed — the builder only
keeps an explicit allowlist of safe fields (see `SAFE_FIELDS` in
`build.py`), and an optional `PII_BLOCKLIST` env var aborts the build if
a forbidden string ever appears in the output.

## Refresh (for the 20-minute loop)

```bash
cd ~/workspace/dashboard
TOKEN=$(printf 'protocol=https\nhost=github.com\nusername=oauth\n' | git credential fill | sed -n 's/^password=//p')
GITHUB_TOKEN=$TOKEN python3 build.py
unset TOKEN
# sanity: data.json must contain only public repo metadata
git add data.json
git -c user.name=Jack -c user.email=noreply@anthropic.com commit -m "data: dashboard refresh" -q
git push origin master
# publish static site
git checkout --orphan gh-pages-tmp  # or reuse existing gh-pages branch
```

Publishing: the `gh-pages` branch contains only `index.html` + `data.json`
+ `projects/` + `assets/shots/` + `assets/made-by-dogs.webp`
(no build tooling, no secrets). To republish after a refresh:

```bash
cd ~/workspace/dashboard
git checkout gh-pages
cp /tmp/dash-site/index.html /tmp/dash-site/data.json .  # from a fresh build
cp -r /tmp/dash-site/projects /tmp/dash-site/assets .
git add -A
git -c user.name=Jack -c user.email=noreply@anthropic.com commit -m "site: refresh" -q
git push origin gh-pages
git checkout master
```

Simpler: run `GITHUB_TOKEN=$TOKEN python3 build.py` on master, then copy
`index.html` + `data.json` onto the `gh-pages` branch and push.

## Project hub pages

Each tile opens a static hub page instead of GitHub directly:
`projects/<repo>/index.html` (plus `projects/castle/` + module sub-pages
for grouped projects). Hubs show a live-site screenshot slideshow, live
status badge, what's going on (latest commit), status facts, percent
complete, ideas from the repo's own docs, and a conception→updates timeline
from the local clone's git log.

```bash
cd ~/workspace/dashboard
# 1. screenshots of live sites (needs the 127.0.0.1:3129 egress relay up)
/tmp/pwenv/bin/python capture_shots.py
# 2. generate hub pages (PII-guarded; aborts if a blocklisted name leaks in)
python3 build_projects.py
```

- `projects/groups.json` — generic grouping: member repos collapse into one
  tile linking to the group hub (e.g. `castle` + `castle-os` → one Castle
  tile → `projects/castle/` → module sub-pages). Add future groups the same
  way; no code changes needed.
- `projects/meta.json` — percent-complete values. `null` renders "IN BUILD";
  a number is only ever written with a verifiable source recorded next to it.
- `capture_shots.py` — Playwright + system Chromium (`/opt/meta-chromium/chrome`)
  screenshots of every repo with a Pages URL into `assets/shots/<repo>/`.
  Repos with no live site get a pure CSS/SVG title card instead.
- Attribution rule: "This product was made by DOGS" + the DOGS badge appear
  EXACTLY ONCE per page, as a normal scrolling footer element at the
  bottom-right. `build_projects.py` aborts the build if the count differs.

## Files

- `index.html` — static page (vanilla HTML/CSS/JS, no dependencies, mobile-friendly)
- `build.py` — fetches org metadata via the GitHub API, writes `data.json`
- `data.json` — generated snapshot (committed so the loop can diff it)
- `build_projects.py` — bakes `projects/<repo>/index.html` hub pages
- `capture_shots.py` — live-site screenshots for hub slideshows
- `projects/groups.json` — tile→hub grouping config (Castle group)
- `projects/meta.json` — percent-complete sources (all "IN BUILD" until sourced)
