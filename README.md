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

Header carries the the founder / DOGS identity line so the URL works
on a resume; semantic HTML (`<article>` tiles with real links), proper
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
(no build tooling, no secrets). To republish after a refresh:

```bash
cd ~/workspace/dashboard
git checkout gh-pages
cp /tmp/dash-site/index.html /tmp/dash-site/data.json .  # from a fresh build
git add -A
git -c user.name=Jack -c user.email=noreply@anthropic.com commit -m "site: refresh" -q
git push origin gh-pages
git checkout master
```

Simpler: run `GITHUB_TOKEN=$TOKEN python3 build.py` on master, then copy
`index.html` + `data.json` onto the `gh-pages` branch and push.

## Files

- `index.html` — static page (vanilla HTML/CSS/JS, no dependencies, mobile-friendly)
- `build.py` — fetches org metadata via the GitHub API, writes `data.json`
- `data.json` — generated snapshot (committed so the loop can diff it)
