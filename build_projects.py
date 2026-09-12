#!/usr/bin/env python3
"""Watchtower hub-page generator.

Reads data.json (+ projects/groups.json, projects/meta.json) and bakes one
static hub page per project into projects/<slug>/index.html, plus one hub
per group (projects/<group>/) with linked module sub-pages
(projects/<group>/<module>/).

Content sources (all honest, never invented):
  - header/status/links : data.json (Watchtower site status; degrades to
    "not monitored", never a fake green)
  - slideshow           : assets/shots/<slug>/*.{png,webp} captured from the live
    site by capture_shots.py (PNGs re-encoded to webp for weight);
    repos with no live site get a pure CSS/SVG
    title card (no external assets)
  - what's going on      : repo description + latest commit (msg + date)
    from the local clone's git log (read-only)
  - timeline            : conception (first commit) -> recent updates, from
    the local clone's git log (read-only)
  - ideas               : bullet lists quoted/paraphrased from the repo's own
    docs (ROADMAP.md, VISION.md, TODO.md, README roadmap sections, ...)
  - percent complete    : projects/meta.json only; null renders "IN BUILD".
    A number is never written without a verifiable source.

Local clones (read-only) are resolved from ~/workspace/org-audit/<repo>,
~/workspace/icecream-inspect (icecream), and /tmp/hubclones/<repo>
(with /tmp/hubclones/root for yeahdogs.github.io).

PII guard: every generated page is scanned for blocklisted personal-name
tokens before it is written; the build aborts on any hit.

Attribution rule (hard): the DOGS badge image, linking to
https://wearedogs.net, appears EXACTLY ONCE per page, as a normal scrolling
footer element at the bottom. No other footer text or links. Never fixed,
never in the header, never duplicated.
"""

import glob
import html
import json
import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_JSON = os.path.join(HERE, "data.json")
GROUPS_JSON = os.path.join(HERE, "projects", "groups.json")
META_JSON = os.path.join(HERE, "projects", "meta.json")
PROJECTS_DIR = os.path.join(HERE, "projects")
SHOTS_DIR = os.path.join(HERE, "assets", "shots")
GEN_DIR = os.path.join(HERE, "assets", "gen")
BADGE = "../../assets/made-by-dogs.webp"

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
    "6a378eb66407c6aa2767e3319d47009d668804b5fbd803b6b11fdcb12f275552",
})


def redact_pii(text):
    """Replace blocklisted personal-name tokens with [redacted].

    Hub pages ingest arbitrary repo history (commit subjects, branch names)
    that can contain a leaked name variant. The blocklist is stored as
    SHA-256 digests only — the plaintext never appears in source. This
    scans 1-3 word n-grams exactly like pii_guard and substitutes matches,
    so generated pages can never republish a leak. The guard still runs
    after this as verification.
    """
    digests = set(_PII_DIGESTS)
    for tok in os.environ.get("PII_BLOCKLIST", "").split(","):
        tok = tok.strip().lower()
        if tok:
            digests.add(hashlib.sha256(tok.encode()).hexdigest())
    words = [(m.group(), m.start(), m.end())
             for m in re.finditer(r"[a-z0-9]+", text.lower())]
    spans = []
    for n in (1, 2, 3):
        for i in range(len(words) - n + 1):
            cand = " ".join(w[0] for w in words[i:i + n])
            if hashlib.sha256(cand.encode()).hexdigest() in digests:
                spans.append((words[i][1], words[i + n - 1][2]))
    if not spans:
        return text
    spans.sort()
    merged = [spans[0]]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    out = []
    pos = 0
    for s, e in merged:
        out.append(text[pos:s])
        out.append("[redacted]")
        pos = e
    out.append(text[pos:])
    return "".join(out)


def pii_guard(text, where):
    digests = set(_PII_DIGESTS)
    for tok in os.environ.get("PII_BLOCKLIST", "").split(","):
        tok = tok.strip().lower()
        if tok:
            digests.add(hashlib.sha256(tok.encode()).hexdigest())
    words = re.findall(r"[a-z0-9]+", text.lower())
    for n in (1, 2, 3):
        for i in range(len(words) - n + 1):
            cand = " ".join(words[i:i + n])
            if hashlib.sha256(cand.encode()).hexdigest() in digests:
                sys.exit("PII_GUARD: blocklisted personal-name token in %s; aborting." % where)


def esc(s):
    return html.escape(s or "", quote=True)


# ---------------------------------------------------------------- clones

CLONE_BASES = [
    os.path.expanduser("~/workspace/org-audit"),
    "/tmp/hubclones",
]
CLONE_ALIASES = {
    "icecream": [os.path.expanduser("~/workspace/icecream-inspect")],
    "yeahdogs.github.io": ["/tmp/hubclones/root"],
}


def find_clone(repo):
    for base in CLONE_ALIASES.get(repo, []):
        if os.path.isdir(os.path.join(base, ".git")):
            return base
    for base in CLONE_BASES:
        p = os.path.join(base, repo)
        if os.path.isdir(os.path.join(p, ".git")):
            return p
    return None


def git_log(repo_dir, fmt, args=()):
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "log", "--format=" + fmt] + list(args),
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []
    return [l for l in out.stdout.splitlines() if l.strip()]


def latest_commit(repo_dir):
    lines = git_log(repo_dir, "%ad|%s", ["--date=short", "-n", "1"])
    if not lines:
        return None
    date, _, subject = lines[0].partition("|")
    return {"date": date.strip(), "subject": subject.strip()}


def timeline(repo_dir, keep=8):
    lines = git_log(repo_dir, "%h|%ad|%s", ["--date=short", "--reverse"])
    entries = []
    for l in lines:
        h, _, rest = l.partition("|")
        date, _, subject = rest.partition("|")
        entries.append({"hash": h.strip(), "date": date.strip(), "subject": subject.strip()})
    if not entries:
        return []
    picked = [entries[0]]
    for e in entries[1:]:
        if e["hash"] != picked[-1]["hash"]:
            picked.append(e)
    picked = [picked[0]] + picked[-keep:] if len(picked) > keep + 1 else picked
    seen, out = set(), []
    for e in picked:
        if e["hash"] not in seen:
            seen.add(e["hash"])
            out.append(e)
    return out


# ---------------------------------------------------------------- ideas

IDEA_FILENAMES = ["ROADMAP.md", "VISION.md", "TODO.md", "IDEAS.md", "PLAN.md"]
SECTION_RE = re.compile(r"^\s*#{1,4}\s*(.+?)\s*$")
BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(.+?)\s*$")
IDEA_SECTION_RE = re.compile(r"roadmap|vision|ideas|to[- ]?do|future|planned|next up", re.I)


def clean_md(text):
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)      # links -> text
    text = re.sub(r"[*_`~]+", "", text)                        # emphasis
    text = re.sub(r"\s+", " ", text).strip()
    return text[:160]


def ideas_from_file(path, max_items=8):
    items = []
    try:
        with open(path, errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return items
    in_section = os.path.basename(path).upper() in [n.upper() for n in IDEA_FILENAMES]
    for line in lines:
        m = SECTION_RE.match(line)
        if m:
            in_section = bool(IDEA_SECTION_RE.search(m.group(1)))
            continue
        if in_section:
            b = BULLET_RE.match(line)
            if b:
                t = clean_md(b.group(1))
                if len(t) > 12:
                    items.append(t)
                if len(items) >= max_items:
                    break
    return items


def collect_ideas(repo_dir, max_items=6):
    found = []
    if not repo_dir:
        return found
    for name in IDEA_FILENAMES:
        p = os.path.join(repo_dir, name)
        if os.path.isfile(p):
            for t in ideas_from_file(p):
                found.append((t, name))
    docs = os.path.join(repo_dir, "docs")
    if os.path.isdir(docs):
        for p in sorted(glob.glob(os.path.join(docs, "*.md"))):
            for t in ideas_from_file(p):
                found.append((t, "docs/" + os.path.basename(p)))
    rp = os.path.join(repo_dir, "README.md")
    if os.path.isfile(rp):
        for t in ideas_from_file(rp):
            found.append((t, "README.md"))
    seen, out = set(), []
    for t, src in found:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append({"text": t, "source": src})
        if len(out) >= max_items:
            break
    return out


# ---------------------------------------------------------------- pieces

def status_badge(site):
    if site and site.get("status") == "up":
        return '<span class="badge up">● LIVE</span>'
    if site and site.get("status") == "down":
        return '<span class="badge down">● DOWN</span>'
    return '<span class="badge na">○ NOT MONITORED</span>'


def percent_block(meta):
    pct = (meta or {}).get("percent_complete")
    if isinstance(pct, (int, float)):
        p = max(0, min(100, int(pct)))
        src = esc((meta or {}).get("source") or "")
        return (
            '<div class="pct"><div class="pct-bar"><i style="width:%d%%"></i></div>'
            '<div class="pct-row"><b>%d%%</b><span>Source: %s</span></div></div>'
            % (p, p, src)
        )
    return (
        '<div class="pct"><span class="inbuild">IN BUILD</span>'
        '<p class="fine">No completion estimate published yet. A percentage is only '
        'shown here with a verifiable source.</p></div>'
    )


def title_card(name, caption):
    return (
        '<div class="slide titlecard" role="img" aria-label="%s">'
        '<svg viewBox="0 0 800 450" preserveAspectRatio="xMidYMid slice">'
        '<rect width="800" height="450" fill="#0b0e14"/>'
        '<rect x="24" y="24" width="752" height="402" fill="none" stroke="#232b3a" stroke-width="2"/>'
        '<text x="400" y="180" text-anchor="middle" fill="#e6e9f0" font-size="64" font-weight="800" '
        'font-family="Impact,Haettenschweiler,\'Arial Narrow\',sans-serif" letter-spacing="2">%s</text>'
        '<text x="400" y="240" text-anchor="middle" fill="#8b93a7" font-size="28" '
        'font-family="Impact,Haettenschweiler,\'Arial Narrow\',sans-serif" letter-spacing="6">DOGS</text>'
        '<text x="400" y="300" text-anchor="middle" fill="#5b6478" font-size="20" '
        'font-family="Verdana,sans-serif">%s</text>'
        '</svg></div>'
        % (esc(caption), esc(name.upper()), esc(caption))
    )


CAPTIONS = {
    "yeahdogs.github.io": [
        "Forwards to the Watchtower — desktop",
        "Forwards to the Watchtower — desktop, scrolled",
        "Forwards to the Watchtower — mobile",
    ],
}
DEFAULT_CAPTIONS = ["Live site — desktop", "Live site — desktop, scrolled", "Live site — mobile (390px)"]


SITE_BASE = "https://yeahdogs.github.io/tower"
FAVICON = SITE_BASE + "/assets/favicon.svg"


def social_img(slug):
    """Absolute URL of the best share image for a detail page, or \"\".

    Prefers the first live-site screenshot (desktop), falls back to the
    generated key-art banner. Detail pages live under /tower/projects/,
    so images resolve off SITE_BASE."""
    hits = sorted(glob.glob(os.path.join(SHOTS_DIR, slug, "*.webp")) +
                  glob.glob(os.path.join(SHOTS_DIR, slug, "*.png")))
    if hits:
        return "%s/assets/shots/%s/%s" % (SITE_BASE, slug, os.path.basename(hits[0]))
    hits = sorted(glob.glob(os.path.join(GEN_DIR, slug, "hero.*")))
    if hits:
        return "%s/assets/gen/%s/%s" % (SITE_BASE, slug, os.path.basename(hits[0]))
    return ""


def social_meta(title, desc, url_path, slug):
    """Open Graph + Twitter Card tags for a detail page.

    Title/description mirror the <title> and meta description; no extra copy."""
    lines = [
        '<meta property="og:type" content="article">',
        '<meta property="og:title" content="%s">' % esc(title),
        '<meta property="og:description" content="%s">' % esc(desc),
        '<meta property="og:url" content="%s">' %
        esc("%s/%s" % (SITE_BASE, url_path.strip("/"))),
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="twitter:title" content="%s">' % esc(title),
        '<meta name="twitter:description" content="%s">' % esc(desc),
    ]
    img = social_img(slug)
    if img:
        lines.append('<meta property="og:image" content="%s">' % esc(img))
        lines.append('<meta name="twitter:image" content="%s">' % esc(img))
    return "\n".join(lines)


def hero_art(slug):
    """Generated key-art banner for a hub page, or "" when absent.

    Reads assets/gen/<slug>/hero.* written by the media pipeline. The
    banner is pure imagery — no copy, per the minimal-words rule."""
    hits = sorted(glob.glob(os.path.join(GEN_DIR, slug, "hero.*")))
    if not hits:
        return ""
    rel = "../../assets/gen/%s/%s" % (slug, os.path.basename(hits[0]))
    return ('<div class="hero-art"><img src="%s" alt="%s key art" loading="eager">'
            "</div>" % (esc(rel), esc(slug)))


def slideshow(slug, name):
    files = sorted(glob.glob(os.path.join(SHOTS_DIR, slug, "*.png")) +
                   glob.glob(os.path.join(SHOTS_DIR, slug, "*.webp")))
    caps = CAPTIONS.get(slug, DEFAULT_CAPTIONS)
    if not files:
        return title_card(name, "No live site yet — title card")
    slides = []
    for i, f in enumerate(files):
        cap = caps[i] if i < len(caps) else "Live site"
        rel = "../../assets/shots/%s/%s" % (slug, os.path.basename(f))
        slides.append(
            '<figure class="slide"><img src="%s" alt="%s" loading="lazy">'
            '<figcaption>%s</figcaption></figure>' % (esc(rel), esc(cap), esc(cap))
        )
    dots = "".join(
        '<button class="sdot" data-i="%d" aria-label="Slide %d"></button>' % (i, i + 1)
        for i in range(len(slides))
    )
    return (
        '<div class="slides" data-slides="%d">'
        '<div class="track">%s</div>'
        '<button class="snav prev" aria-label="Previous slide">‹</button>'
        '<button class="snav next" aria-label="Next slide">›</button>'
        '<div class="scount"><span class="cur">1</span> / %d</div>'
        '<div class="sdots">%s</div>'
        '</div>' % (len(slides), "".join(slides), len(slides), dots)
    )


def videos_section(repos):
    """'Videos' section from kind=='video' progress entries (repo-root-relative mp4s).

    Detail pages live two levels under the repo root (projects/<slug>/),
    so urls like 'demos/01-foo.mp4' become '../../demos/01-foo.mp4'.
    Returns '' when no repo has a demo video.
    """
    vids = []
    for r in repos:
        for e in r.get("progress", []):
            url = (e.get("url") or "")
            if e.get("kind") == "video" and url.endswith(".mp4") \
                    and not url.startswith(("http://", "https://", "/")):
                vids.append((
                    "../../" + url,
                    e.get("title") or "Demo",
                    e.get("detail") or "",
                ))
    if not vids:
        return ""
    items = "".join(
        '<figure><video controls preload="metadata" src="%s"></video>'
        '<figcaption><strong>%s</strong><span>%s</span></figcaption></figure>'
        % (esc(src), esc(title), esc(detail)) for src, title, detail in vids)
    return section("Videos", '<div class="vids%s">%s</div>'
                   % (" two" if len(vids) > 1 else "", items))

CSS = """
:root{
  --bg:#000; --panel:#0b0e14; --line:#232b3a; --text:#f5f5f5;
  --muted:#9aa0ae; --dim:#5b6478; --green:#3ddc84; --red:#ff5c5c;
  --display:Impact,Haettenschweiler,"Arial Narrow","Franklin Gothic Medium",sans-serif;
  --body:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--body);
  padding:26px 18px 40px;max-width:1080px;margin:0 auto;-webkit-font-smoothing:antialiased}
a{color:inherit}
.kicker{font-family:var(--display);letter-spacing:.28em;font-size:.78rem;color:var(--muted);
  text-transform:uppercase;margin-bottom:10px}
.kicker a{color:var(--muted);text-decoration:none;border-bottom:1px solid var(--line)}
.dossier-head h1{font-family:var(--display);font-size:clamp(2.6rem,9vw,5rem);
  text-transform:uppercase;letter-spacing:.02em;line-height:.95;margin-bottom:12px}
.lede{color:var(--muted);font-size:1.02rem;line-height:1.55;max-width:62ch;margin-bottom:16px}
.badges{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:18px}
.badge{font-family:var(--display);font-size:.82rem;letter-spacing:.14em;
  border:1px solid var(--line);border-radius:999px;padding:7px 14px;text-transform:uppercase}
.badge.up{color:var(--green);border-color:var(--green)}
.badge.down{color:var(--red);border-color:var(--red)}
.badge.na{color:var(--muted)}
.actions{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:8px}
.btn{font-family:var(--display);letter-spacing:.1em;text-transform:uppercase;font-size:.85rem;
  text-decoration:none;border:1px solid var(--text);border-radius:6px;padding:10px 18px;
  background:var(--text);color:#000}
.btn.ghost{background:transparent;color:var(--text)}
.btn:hover{opacity:.85}
main{margin-top:26px;display:grid;gap:18px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:22px}
.panel>h2{font-family:var(--display);font-size:1.35rem;letter-spacing:.12em;text-transform:uppercase;
  border-left:6px solid var(--text);padding-left:12px;margin-bottom:16px}
/* key art hero */
.hero-art{margin:0 0 6px;border-radius:12px;overflow:hidden;border:1px solid var(--line)}
.hero-art img{width:100%;display:block;aspect-ratio:21/9;object-fit:cover}
/* slideshow */
.slides{position:relative;overflow:hidden;border-radius:8px;border:1px solid var(--line);background:#000}
.track{display:flex;transition:transform .35s ease}
.slide{min-width:100%}
.slide img{width:100%;display:block;aspect-ratio:16/10;object-fit:cover;object-position:top}
.slide.titlecard svg{width:100%;display:block}
.slide figcaption{font-size:.78rem;color:var(--muted);padding:10px 14px;border-top:1px solid var(--line)}
.snav{position:absolute;top:38%;background:rgba(0,0,0,.65);color:#fff;border:1px solid var(--line);
  width:42px;height:42px;border-radius:50%;font-size:1.4rem;cursor:pointer;line-height:1}
.snav.prev{left:10px}.snav.next{right:10px}
.scount{position:absolute;top:10px;right:12px;background:rgba(0,0,0,.65);border:1px solid var(--line);
  border-radius:999px;padding:4px 12px;font-size:.75rem;color:var(--muted)}
.sdots{display:flex;gap:8px;justify-content:center;padding:12px}
.sdot{width:10px;height:10px;border-radius:50%;border:1px solid var(--dim);background:transparent;cursor:pointer}
.sdot[aria-current="true"]{background:var(--text);border-color:var(--text)}
/* whats going on */
.commit{background:#000;border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-top:12px}
.commit .msg{font-weight:600;margin-bottom:6px;line-height:1.45}
.commit .when{font-size:.8rem;color:var(--dim)}
/* status facts */
.facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.fact{border:1px solid var(--line);border-radius:8px;padding:12px;background:#000}
.fact .k{font-size:.68rem;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);margin-bottom:6px}
.fact .v{font-weight:600;font-size:.95rem}
.fact .v.ok{color:var(--green)} .fact .v.bad{color:var(--red)}
/* percent */
.pct-bar{height:14px;border:1px solid var(--line);border-radius:999px;overflow:hidden;background:#000;margin-bottom:10px}
.pct-bar i{display:block;height:100%;background:var(--text)}
.pct-row{display:flex;justify-content:space-between;gap:10px;font-size:.85rem;color:var(--muted)}
.inbuild{display:inline-block;font-family:var(--display);letter-spacing:.18em;font-size:1.1rem;
  border:2px solid var(--text);border-radius:8px;padding:10px 20px;margin-bottom:10px}
.fine{font-size:.85rem;color:var(--muted);line-height:1.5;max-width:60ch}
/* ideas */
.ideas{list-style:none;display:grid;gap:10px}
.ideas li{border-left:3px solid var(--text);padding:8px 0 8px 14px;line-height:1.5}
.ideas .src{display:block;font-size:.72rem;color:var(--dim);margin-top:4px;letter-spacing:.06em}
.empty-note{color:var(--muted);line-height:1.6}
/* timeline */
.timeline{list-style:none;position:relative;padding-left:26px}
.timeline::before{content:"";position:absolute;left:8px;top:6px;bottom:6px;width:2px;background:var(--line)}
.timeline li{position:relative;padding:0 0 18px}
.timeline li::before{content:"";position:absolute;left:-24px;top:5px;width:12px;height:12px;
  border-radius:50%;background:var(--bg);border:2px solid var(--text)}
.timeline li.first::before{background:var(--text)}
.timeline .t{font-size:.75rem;color:var(--dim);letter-spacing:.08em}
.timeline .s{font-weight:600;line-height:1.45;margin-top:2px}
.timeline .tag{display:inline-block;font-family:var(--display);font-size:.68rem;letter-spacing:.16em;
  border:1px solid var(--text);border-radius:4px;padding:2px 8px;margin-left:8px;vertical-align:middle}
/* module cards */
.mods{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.mod{border:1px solid var(--line);border-radius:8px;padding:18px;background:#000;text-decoration:none;
  display:block;transition:border-color .15s ease,transform .15s ease}
.mod:hover{border-color:var(--text);transform:translateY(-3px)}
.mod h3{font-family:var(--display);letter-spacing:.06em;text-transform:uppercase;font-size:1.15rem;margin-bottom:8px}
.mod p{color:var(--muted);font-size:.88rem;line-height:1.5}
.mod .go{display:inline-block;margin-top:12px;font-size:.78rem;letter-spacing:.14em;
  font-family:var(--display);text-transform:uppercase;border-bottom:1px solid var(--text)}
/* motion: header entrance, scroll-reveal sections, staggered module cards */
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:translateY(0)}}
.dossier-head{animation:rise .5s ease both}
.reveal{opacity:0;transform:translateY(18px);transition:opacity .55s ease,transform .55s ease}
.reveal.in{opacity:1;transform:none}
.cardin{animation:rise .45s ease backwards}
/* key-art hero entrance: cinematic scale-settle after the header rises */
@keyframes heroIn{from{opacity:0;transform:scale(1.06)}to{opacity:1;transform:scale(1)}}
.hero-art{animation:heroIn .9s ease .15s both}
/* videos */
.vids{display:grid;gap:20px}
.vids.two{grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.vids figure{margin:0}
.vids video{width:100%;border-radius:8px;border:1px solid var(--line);background:#000}
.vids figcaption{margin-top:8px}
.vids figcaption strong{display:block;font-size:.95rem;color:var(--text)}
.vids figcaption span{display:block;font-size:.85rem;color:var(--muted);margin-top:2px}
/* footer: THE one attribution — bottom-right, scrolls with the page */
.dogs-foot{margin-top:34px;text-align:right}
.dogs-foot a{display:inline-block;text-decoration:none}
.dogs-foot img{display:block;max-width:230px;width:100%;height:auto;margin:0 0 8px auto}
.dogs-foot span{font-size:.8rem;color:var(--muted);letter-spacing:.02em}
.dogs-foot b{color:var(--text)}
@media (prefers-reduced-motion: reduce){
  .track{transition:none}
  .mod{transition:none}
  .snav,.sdot{transition:none}
  .dossier-head{animation:none}
  .reveal{opacity:1;transform:none;transition:none}
  .cardin{animation:none}
  .hero-art{animation:none}
  .mod:hover{transform:none}
}
@media (max-width:560px){
  body{padding:18px 12px 32px}
  .panel{padding:16px}
  .slide img{aspect-ratio:4/5;object-position:top}
  .snav{top:30%}
  .dogs-foot img{max-width:180px}
}
"""

JS = """
(function(){
  document.querySelectorAll('.slides[data-slides]').forEach(function(box){
    var n=+box.getAttribute('data-slides'), i=0;
    var track=box.querySelector('.track'), cur=box.querySelector('.cur');
    var dots=Array.prototype.slice.call(box.querySelectorAll('.sdot'));
    function go(k){ i=(k+n)%n; track.style.transform='translateX(-'+(i*100)+'%)';
      cur.textContent=i+1;
      dots.forEach(function(d,j){ d.setAttribute('aria-current', j===i?'true':'false'); }); }
    box.querySelector('.prev').addEventListener('click',function(){takeover();go(i-1)});
    box.querySelector('.next').addEventListener('click',function(){takeover();go(i+1)});
    dots.forEach(function(d,j){ d.addEventListener('click',function(){takeover();go(j)}); });
    var x0=null;
    box.addEventListener('touchstart',function(e){x0=e.touches[0].clientX},{passive:true});
    box.addEventListener('touchend',function(e){ if(x0===null)return;
      var dx=e.changedTouches[0].clientX-x0; if(Math.abs(dx)>40){takeover();go(i+(dx<0?1:-1));} x0=null; },{passive:true});
    // auto-advance: hands-off cinematic drift, 6s per slide. Killed entirely
    // under prefers-reduced-motion; paused while hovered/touched; permanently
    // handed to the visitor on their first manual interaction.
    var reduceA = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var timer=null, owned=true;
    function start(){ if(!owned||timer||reduceA||n<2) return;
      timer=setInterval(function(){ if(!document.hidden) go(i+1); }, 6000); }
    function stop(){ if(timer){ clearInterval(timer); timer=null; } }
    function takeover(){ owned=false; stop(); }
    box.addEventListener('pointerenter', stop);
    box.addEventListener('pointerleave', start);
    document.addEventListener('visibilitychange', function(){
      if(document.hidden) stop(); else start(); });
    start();
    go(0);
  });

  // motion: scroll-reveal sections + staggered module-card entrance.
  // classes are added from JS only, so no-JS visitors never see hidden content.
  (function(){
    var reduceM = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if(!reduceM && ('IntersectionObserver' in window)){
      var secs = document.querySelectorAll('main .panel');
      var io = new IntersectionObserver(function(es){
        es.forEach(function(en){
          if(en.isIntersecting){ en.target.classList.add('in'); io.unobserve(en.target); }
        });
      }, {rootMargin:'0px 0px -6% 0px', threshold:0.06});
      Array.prototype.forEach.call(secs, function(s){
        s.classList.add('reveal'); io.observe(s);
      });
    }
    var cards = document.querySelectorAll('.mods .mod');
    Array.prototype.forEach.call(cards, function(c, i){
      c.style.animationDelay = Math.min(i*45, 700) + 'ms';
      c.classList.add('cardin');
    });
  })();
})();
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — DOGS Project Dossier</title>
<meta name="description" content="{meta_desc}">
<link rel="icon" type="image/svg+xml" href="{favicon}">
<meta name="theme-color" content="#0b0e14">
{social}
<style>{css}</style>
</head>
<body>
<header class="dossier-head">
  <p class="kicker"><a href="{home}">Watchtower</a>{crumb}</p>
  <h1>{name}</h1>
  <p class="lede">{lede}</p>
  <div class="badges">{badge}{pct_pill}</div>
  <div class="actions">{buttons}</div>
</header>
{hero}
<main>
{sections}
</main>
<footer class="dogs-foot">
  <a href="https://wearedogs.net"><img src="{badge_src}" alt="DOGS"></a>
</footer>
<script>{js}</script>
</body>
</html>
"""


def section(title, body):
    return '<section class="panel"><h2>%s</h2>%s</section>' % (esc(title), body)


def footer_only(html_text, where):
    # the badge (linking to wearedogs.net) must appear exactly once per page
    pii_guard(html_text, where)
    if html_text.lower().count("made-by-dogs.webp") != 1:
        sys.exit("ATTRIBUTION: badge image must appear exactly once in %s; aborting." % where)
    if html_text.lower().count("https://wearedogs.net") != 1:
        sys.exit("ATTRIBUTION: wearedogs.net link must appear exactly once in %s; aborting." % where)


def write_page(path, html_text):
    html_text = redact_pii(html_text)
    footer_only(html_text, path)
    pii_guard(html_text, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(html_text)
    print("wrote", os.path.relpath(path, HERE))

# ---------------------------------------------------------------- builders

def repo_hub(repo, meta):
    name = repo["name"]
    clone = find_clone(name)
    lc = latest_commit(clone) if clone else None
    tl = timeline(clone) if clone else []
    ideas = collect_ideas(clone) if clone else []

    badge = status_badge(repo.get("site"))
    pct_meta = (meta.get("repos") or {}).get(name, {})
    buttons = ""
    if repo.get("pages"):
        buttons += '<a class="btn" href="%s">Live site</a>' % esc(repo["pages"])
    buttons += '<a class="btn ghost" href="%s">GitHub repo</a>' % esc(repo["url"])

    if lc:
        going = ('<p class="lede">%s</p>' % esc(repo.get("description") or "No description published.")
                 + '<div class="commit"><p class="msg">%s</p>'
                   '<p class="when">latest commit · %s</p></div>' % (esc(lc["subject"]), esc(lc["date"])))
    else:
        pushed = (repo.get("pushed_at") or "")[:10]
        going = ('<p class="lede">%s</p>' % esc(repo.get("description") or "No description published.")
                 + '<div class="commit"><p class="msg">History snapshot not available in this build '
                   'environment.</p><p class="when">last push · %s</p></div>' % esc(pushed))

    facts = [
        ("Live status", ("LIVE" if (repo.get("site") or {}).get("status") == "up"
                         else "DOWN" if (repo.get("site") or {}).get("status") == "down"
                         else "not monitored"),
         "ok" if (repo.get("site") or {}).get("status") == "up"
         else "bad" if (repo.get("site") or {}).get("status") == "down" else ""),
        ("Language", repo.get("language") or "—", ""),
        ("Stars", str(repo.get("stars", 0)), ""),
        ("Open issues", str(repo.get("open_issues", 0)), ""),
        ("Open PRs", str(repo.get("open_prs", 0)), ""),
        ("Commits", str(repo.get("commits", "—")), ""),
        ("Last push", (repo.get("pushed_at") or "—")[:10], ""),
    ]
    wf = repo.get("workflow") or {}
    if wf.get("conclusion"):
        facts.append(("Last workflow", "%s · %s" % (wf.get("name") or "workflow", wf["conclusion"]), ""))
    facts_html = '<div class="facts">' + "".join(
        '<div class="fact"><p class="k">%s</p><p class="v %s">%s</p></div>'
        % (esc(k), cls, esc(v)) for k, v, cls in facts) + "</div>"

    if ideas:
        ideas_html = '<ul class="ideas">' + "".join(
            '<li>%s<span class="src">from %s</span></li>' % (esc(i["text"]), esc(i["source"]))
            for i in ideas) + "</ul>"
    else:
        ideas_html = ('<p class="empty-note">No published roadmap yet — ideas are still '
                      'in the build log. Nothing here is invented.</p>')

    if tl:
        items = []
        for j, e in enumerate(tl):
            tag = '<span class="tag">Conception</span>' if j == 0 else ""
            items.append('<li%s><p class="t">%s</p><p class="s">%s%s</p></li>'
                         % (' class="first"' if j == 0 else "", esc(e["date"]),
                            esc(e["subject"]), tag))
        tl_html = '<ol class="timeline">' + "".join(items) + "</ol>"
    else:
        tl_html = '<p class="empty-note">Timeline unavailable — no local history in this build environment.</p>'

    sections = "\n".join(filter(None, [
        section("Slideshow", slideshow(name, name)),
        videos_section([repo]),
        section("What's going on", going),
        section("Status", facts_html),
        section("Percent complete", percent_block(pct_meta)),
        section("Ideas", ideas_html),
        section("Timeline", tl_html),
    ]))
    pct = pct_meta.get("percent_complete")
    pct_pill = ('<span class="badge">%d%%</span>' % pct) if isinstance(pct, (int, float)) \
        else '<span class="badge">In build</span>'
    title_full = "%s — DOGS Project Dossier" % name
    desc = ("DOGS project dossier: %s. %s" % (name, repo.get("description") or ""))[:160]
    return PAGE.format(
        title=esc(name), meta_desc=esc(desc),
        social=social_meta(title_full, desc, "projects/%s/" % name, name),
        favicon=FAVICON,
        css=CSS, home="../../", crumb=" / " + esc(name), name=esc(name),
        lede=esc(repo.get("description") or "No description published."),
        badge=badge, pct_pill=pct_pill, buttons=buttons,
        hero=hero_art(name),
        sections=sections, badge_src=BADGE, js=JS,
    )


def group_hub(slug, group, repos_by_name, meta):
    members = [repos_by_name[m] for m in group.get("members", []) if m in repos_by_name]
    clones = [(m, find_clone(m)) for m in group.get("members", [])]
    primary = next((c for _, c in clones if c), None)
    tl = timeline(primary) if primary else []
    ideas = []
    for m, c in clones:
        if c:
            for i in collect_ideas(c, max_items=4):
                ideas.append({"text": i["text"], "source": "%s:%s" % (m, i["source"])})
    ideas = ideas[:6]

    lc_parts = []
    for m, c in clones:
        lc = latest_commit(c) if c else None
        if lc:
            lc_parts.append("<b>%s</b> — %s <span class='when'>(%s)</span>"
                            % (esc(m), esc(lc["subject"]), esc(lc["date"])))
    going = ('<p class="lede">%s</p>' % esc(group.get("description") or ""))
    if lc_parts:
        going += '<div class="commit">' + "<br>".join(
            '<p class="msg">%s</p>' % p for p in lc_parts) + "</div>"
    else:
        going += '<p class="empty-note">Module history not available in this build environment.</p>'

    mods = []
    for mod in group.get("modules", []):
        mods.append(
            '<a class="mod" href="%s/"><h3>%s</h3><p>%s</p><span class="go">Open dossier →</span></a>'
            % (esc(mod["slug"]), esc(mod["title"]), esc(mod.get("blurb") or "")))
    mods_html = '<div class="mods">' + "".join(mods) + "</div>"

    if ideas:
        ideas_html = '<ul class="ideas">' + "".join(
            '<li>%s<span class="src">from %s</span></li>' % (esc(i["text"]), esc(i["source"]))
            for i in ideas) + "</ul>"
    else:
        ideas_html = '<p class="empty-note">No published roadmap yet.</p>'

    if tl:
        items = []
        for j, e in enumerate(tl):
            tag = '<span class="tag">Conception</span>' if j == 0 else ""
            items.append('<li%s><p class="t">%s</p><p class="s">%s%s</p></li>'
                         % (' class="first"' if j == 0 else "", esc(e["date"]),
                            esc(e["subject"]), tag))
        tl_html = '<ol class="timeline">' + "".join(items) + "</ol>"
    else:
        tl_html = '<p class="empty-note">Timeline unavailable in this build environment.</p>'

    repo_buttons = "".join(
        '<a class="btn ghost" href="%s">%s on GitHub</a>' % (esc(r["url"]), esc(r["name"]))
        for r in members)
    pct_meta = (meta.get("groups") or {}).get(slug, {})
    pct = pct_meta.get("percent_complete")
    pct_pill = ('<span class="badge">%d%%</span>' % pct) if isinstance(pct, (int, float)) \
        else '<span class="badge">In build</span>'

    sections = "\n".join(filter(None, [
        section("Slideshow", slideshow("castle", group["title"])),
        videos_section(members),
        section("What's going on", going),
        section("Modules", mods_html),
        section("Percent complete", percent_block(pct_meta)),
        section("Ideas", ideas_html),
        section("Timeline", tl_html),
    ]))
    title_full = "%s — DOGS Project Dossier" % group["title"]
    desc = ("DOGS project dossier: %s. %s"
            % (group["title"], group.get("tagline") or ""))[:160]
    return PAGE.format(
        title=esc(group["title"]),
        meta_desc=esc(desc),
        social=social_meta(title_full, desc, "projects/%s/" % slug, slug),
        css=CSS, home="../../", crumb=" / " + esc(group["title"]),
        favicon=FAVICON,
        name=esc(group["title"]),
        lede=esc(group.get("tagline") or "") + ("<br>" if group.get("tagline") else "")
        + esc(group.get("description") or ""),
        badge='<span class="badge na">○ %d modules</span>' % len(group.get("modules", [])),
        pct_pill=pct_pill, buttons=repo_buttons,
        hero=hero_art(slug),
        sections=sections, badge_src=BADGE, js=JS,
    )


def module_page(group_slug, group, mod, meta):
    repo = mod.get("repo")
    clone = find_clone(repo) if repo else None
    shot_slug = "%s-%s" % (group_slug, mod["slug"])

    going = '<p class="lede">%s</p>' % esc(mod.get("blurb") or "")
    if repo == "castle-os" and clone:
        # castle-os gets the full treatment from its own repo history
        lc = latest_commit(clone)
        if lc:
            going += ('<div class="commit"><p class="msg">%s</p>'
                      '<p class="when">latest commit · %s</p></div>'
                      % (esc(lc["subject"]), esc(lc["date"])))
        tl = timeline(clone)
        ideas = collect_ideas(clone)
    else:
        tl, ideas = [], []
        going += ('<p class="empty-note">Tracked with the Castle repo — see the '
                  '<a href="../">Castle hub</a> for timeline and ideas.</p>')

    vids = mod.get("videos") or []
    vids_html = ""
    if vids:
        vids_html = '<div class="vids">' + "".join(
            '<video controls preload="metadata" src="%s"></video>' % esc(v) for v in vids) + "</div>"

    ideas_html = ('<ul class="ideas">' + "".join(
        '<li>%s<span class="src">from %s</span></li>' % (esc(i["text"]), esc(i["source"]))
        for i in ideas) + "</ul>") if ideas else \
        '<p class="empty-note">No published roadmap yet.</p>'
    tl_html = ('<ol class="timeline">' + "".join(
        '<li%s><p class="t">%s</p><p class="s">%s%s</p></li>'
        % (' class="first"' if j == 0 else "", esc(e["date"]), esc(e["subject"]),
           '<span class="tag">Conception</span>' if j == 0 else "")
        for j, e in enumerate(tl)) + "</ol>") if tl else \
        '<p class="empty-note">Timeline lives on the Castle hub.</p>'

    pct_meta = (meta.get("modules") or {}).get("%s/%s" % (group_slug, mod["slug"]), {})
    pct = pct_meta.get("percent_complete")
    pct_pill = ('<span class="badge">%d%%</span>' % pct) if isinstance(pct, (int, float)) \
        else '<span class="badge">In build</span>'

    sections_list = [
        section("Pictures", slideshow(shot_slug, mod["title"])),
        section("What's going on", going),
    ]
    if vids_html:
        sections_list.append(section("Videos", vids_html))
    sections_list += [
        section("Percent complete", percent_block(pct_meta)),
        section("Ideas", ideas_html),
        section("Timeline", tl_html),
    ]
    title_full = "%s — DOGS Project Dossier" % mod["title"]
    desc = ("DOGS project dossier: %s (a Castle module). %s"
            % (mod["title"], mod.get("blurb") or ""))[:160]
    return PAGE.format(
        title=esc(mod["title"]),
        meta_desc=esc(desc),
        social=social_meta(title_full, desc,
                           "projects/%s/%s/" % (group_slug, mod["slug"]), shot_slug),
        css=CSS, home="../../",
        favicon=FAVICON,
        crumb=' / <a href="../" style="color:var(--muted)">%s</a> / %s'
              % (esc(group["title"]), esc(mod["title"])),
        name=esc(mod["title"]), lede=esc(mod.get("blurb") or ""),
        badge='<span class="badge na">○ Castle module</span>',
        pct_pill=pct_pill,
        buttons='<a class="btn ghost" href="../">← Castle hub</a>',
        hero="",
        sections="\n".join(sections_list), badge_src=BADGE, js=JS,
    )


# ---------------------------------------------------------------- main

def main():
    with open(DATA_JSON) as f:
        data = json.load(f)
    with open(GROUPS_JSON) as f:
        groups = json.load(f).get("groups", {})
    with open(META_JSON) as f:
        meta = json.load(f)

    repos = data["repos"]
    repos_by_name = {r["name"]: r for r in repos}
    grouped_members = set()
    for g in groups.values():
        grouped_members.update(g.get("members", []))

    # repo hubs (ungrouped repos only)
    for r in repos:
        if r["name"] in grouped_members:
            continue
        write_page(os.path.join(PROJECTS_DIR, r["name"], "index.html"), repo_hub(r, meta))

    # group hubs + module sub-pages
    for slug, group in groups.items():
        write_page(os.path.join(PROJECTS_DIR, slug, "index.html"),
                   group_hub(slug, group, repos_by_name, meta))
        for mod in group.get("modules", []):
            write_page(os.path.join(PROJECTS_DIR, slug, mod["slug"], "index.html"),
                       module_page(slug, group, mod, meta))

    print("hub build complete.")


if __name__ == "__main__":
    main()
