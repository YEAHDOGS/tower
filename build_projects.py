#!/usr/bin/env python3
"""Tower portfolio hub-page generator.

Reads data.json (+ projects/groups.json, projects/meta.json,
projects/pitches.json) and bakes one static hub page per project into
projects/<slug>/index.html, plus one hub per group (projects/<group>/) with
linked module sub-pages (projects/<group>/<module>/).

This is the investor-facing portfolio: every page is status-blind. No
uptime badges, no live-site links, no repo stats, no commit history —
nothing that distinguishes an online project from an offline one. One
pitch line per project, from projects/pitches.json.

Content sources (all honest, never invented):
  - lede                : projects/pitches.json (one pitch line per project;
    module pages use the blurb from projects/groups.json)
  - key art             : assets/gen/<slug>/hero.webp, falling back to a pure
    CSS/SVG title card when no key art exists (never hints at site status)
  - slideshow           : assets/shots/<slug>/*.{png,webp} captured from the live
    site by capture_shots.py (PNGs re-encoded to webp for weight)
  - demo videos         : demos/*.mp4 linked from data.json

Status-blind by design: nothing on any page distinguishes an online
project from an offline one. Deployment state is never rendered.

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

try:
    from PIL import Image
except ImportError:  # image dims are optional; build must not break without it
    Image = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_JSON = os.path.join(HERE, "data.json")
GROUPS_JSON = os.path.join(HERE, "projects", "groups.json")
META_JSON = os.path.join(HERE, "projects", "meta.json")
PITCHES_JSON = os.path.join(HERE, "projects", "pitches.json")
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


def load_pitches():
    """projects/pitches.json -> {slug: one-line pitch}. Never raises.

    The single source of project copy for the portfolio: dossier ledes and
    (via index.html) tile taglines. A missing slug falls back to the repo's
    own description at render time."""
    try:
        with open(PITCHES_JSON) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:  # noqa: BLE001 - bad file, fall back
        print("WARN: pitches.json unreadable: %s" % e, file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def pitch_for(pitches, slug, fallback=""):
    p = pitches.get(slug)
    if isinstance(p, str) and p.strip():
        return p.strip()
    return fallback


# ---------------------------------------------------------------- clones

CLONE_BASES = [
    os.path.expanduser("~/workspace/org-audit"),
    "/tmp/hubclones",
]
CLONE_ALIASES = {
    "icecream": [os.path.expanduser("~/workspace/icecream-inspect")],
    "cups": [os.path.expanduser("~/workspace/cups")],
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


def _has_pii_token(text):
    """True if text contains a blocklisted personal-name token (1-3 grams)."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    for n in (1, 2, 3):
        for i in range(len(words) - n + 1):
            cand = " ".join(words[i:i + n])
            if hashlib.sha256(cand.encode()).hexdigest() in _PII_DIGESTS:
                return True
    return False


def timeline(repo_dir, keep=8):
    lines = git_log(repo_dir, "%h|%ad|%s", ["--date=short", "--reverse"])
    entries = []
    for l in lines:
        h, _, rest = l.partition("|")
        date, _, subject = rest.partition("|")
        subject = subject.strip()
        # Portfolio curation: internal privacy-maintenance chores ("remove
        # founder's name", anonymity scrubs) are not project milestones and
        # reference an individual, not the company. Subjects carrying a
        # personal-name token are dropped for the same reason — the public
        # dossier timeline never shows them, scrubbed or otherwise.
        if re.search(r"founder|anonymity", subject, re.I):
            continue
        if _has_pii_token(subject):
            continue
        entries.append({"hash": h.strip(), "date": date.strip(), "subject": subject})
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
        '<div class="pct pct-empty"><span class="inbuild">IN BUILD</span>'
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


def mod_thumb(name):
    """Compact title-card thumbnail for a module link card — image on top in the
    same SVG language as the slideshow title card. Decorative: the link's own
    h3 names the module, so the SVG is aria-hidden. Distinct per module, so no
    card is blank and no art repeats."""
    return (
        '<span class="mthumb" aria-hidden="true">'
        '<svg viewBox="0 0 800 450" preserveAspectRatio="xMidYMid slice">'
        '<rect width="800" height="450" fill="#0b0e14"/>'
        '<rect x="24" y="24" width="752" height="402" fill="none" stroke="#232b3a" stroke-width="2"/>'
        '<text x="400" y="212" text-anchor="middle" fill="#e6e9f0" font-size="72" font-weight="800" '
        'font-family="Impact,Haettenschweiler,\'Arial Narrow\',sans-serif" letter-spacing="2">%s</text>'
        '<text x="400" y="290" text-anchor="middle" fill="#8b93a7" font-size="30" '
        'font-family="Impact,Haettenschweiler,\'Arial Narrow\',sans-serif" letter-spacing="8">DOGS</text>'
        '</svg></span>'
        % esc(name.upper())
    )


CAPTIONS = {
    "yeahdogs.github.io": [
        "Forwards to Tower — desktop",
        "Forwards to Tower — desktop, scrolled",
        "Forwards to Tower — mobile",
    ],
}
DEFAULT_CAPTIONS = ["Desktop", "Desktop, scrolled", "Mobile (390px)"]


SITE_BASE = "https://yeahdogs.github.io/tower"
FAVICON = SITE_BASE + "/assets/favicon.svg"
MANIFEST = SITE_BASE + "/assets/site.webmanifest"
APPLE_TOUCH_ICON = SITE_BASE + "/assets/apple-touch-icon.png"


def social_img(slug):
    """Absolute URL of the best share image for a detail page, or \"\".

    Prefers the first live-site screenshot (desktop), falls back to the
    generated key-art banner. Detail pages live under /tower/projects/,
    so images resolve off SITE_BASE."""
    f = social_img_file(slug)
    if not f:
        return ""
    rel = os.path.relpath(f, os.path.join(HERE, "assets"))
    return "%s/assets/%s" % (SITE_BASE, rel.replace(os.sep, "/"))


def social_img_dims(slug):
    """(width, height) of the best share image for a slug, or None.

    Read with Pillow at build time; None on any failure so the build
    never breaks over an unreadable image."""
    f = social_img_file(slug)
    if not f or Image is None:
        return None
    try:
        with Image.open(f) as im:
            return im.size
    except Exception:
        return None


def social_img_file(slug):
    """Local path of the best share image for a detail page, or "".

    Prefers the first live-site screenshot (desktop), falls back to the
    generated key-art banner."""
    hits = sorted(glob.glob(os.path.join(SHOTS_DIR, slug, "*.webp")) +
                  glob.glob(os.path.join(SHOTS_DIR, slug, "*.png")))
    if hits:
        return hits[0]
    hits = sorted(glob.glob(os.path.join(GEN_DIR, slug, "hero.*")))
    return hits[0] if hits else ""


def share_slug_for(*candidates):
    """First candidate slug that resolves to a share image, or the first.

    Used for pages whose own slug has no screenshot or key art (e.g. Castle
    module sub-pages) so they still get og:image + dims instead of none."""
    for c in candidates:
        if social_img_file(c):
            return c
    return candidates[0]


def social_meta(title, desc, url_path, slug):
    """Open Graph + Twitter Card tags for a detail page.

    Title/description mirror the <title> and meta description; no extra copy.
    Emits a canonical link first so search engines index each dossier at one
    URL (the hub at build.py already has one). og:image carries width/height
    tags so share crawlers can lay out the card without a prefetch.
    og:site_name + og:locale identify the site to share crawlers once per
    page rather than being inferred from each og:title."""
    url = "%s/%s" % (SITE_BASE, url_path.strip("/"))
    lines = [
        '<link rel="canonical" href="%s">' % esc(url),
        '<meta property="og:type" content="article">',
        '<meta property="og:title" content="%s">' % esc(title),
        '<meta property="og:description" content="%s">' % esc(desc),
        '<meta property="og:url" content="%s">' % esc(url),
        '<meta property="og:site_name" content="Tower">',
        '<meta property="og:locale" content="en_US">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="twitter:title" content="%s">' % esc(title),
        '<meta name="twitter:description" content="%s">' % esc(desc),
    ]
    img = social_img(slug)
    if img:
        lines.append('<meta property="og:image" content="%s">' % esc(img))
        dims = social_img_dims(slug)
        if dims:
            lines.append('<meta property="og:image:width" content="%d">' % dims[0])
            lines.append('<meta property="og:image:height" content="%d">' % dims[1])
        lines.append('<meta name="twitter:image" content="%s">' % esc(img))
        alt = "%s: project preview" % title
        lines.append('<meta property="og:image:alt" content="%s">' % esc(alt))
        lines.append('<meta name="twitter:image:alt" content="%s">' % esc(alt))
    return "\n".join(lines)


def json_ld(title, desc, url_path):
    """JSON-LD WebPage block for a detail page.

    Mirrors the hub's Organization block (added 2026-09-11): crawlers get a
    typed WebPage record naming the dossier and tying it to the Watchtower
    WebSite. Invisible, no copy. json.dumps handles escaping — never hand
    HTML-escape the JSON payload (the script tag is raw text)."""
    url = "%s/%s" % (SITE_BASE, url_path.strip("/"))
    data = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": title,
        "url": url,
        "description": desc,
        "isPartOf": {
            "@type": "WebSite",
            "name": "Tower",
            "url": SITE_BASE + "/",
        },
    }
    return '<script type="application/ld+json">\n%s\n</script>' % json.dumps(data)


def hero_art(slug, rel_prefix="../../"):
    """Generated key-art banner for a hub page, or "" when absent.

    Reads assets/gen/<slug>/hero.* written by the media pipeline. The
    banner is pure imagery — no copy, per the minimal-words rule.
    rel_prefix points from the page back to the repo root (module pages
    sit one level deeper)."""
    hits = sorted(glob.glob(os.path.join(GEN_DIR, slug, "hero.*")))
    if not hits:
        return ""
    rel = "%sassets/gen/%s/%s" % (rel_prefix, slug, os.path.basename(hits[0]))
    return ('<div class="hero-art"><img src="%s" alt="%s key art" loading="eager" decoding="async">'
            "</div>" % (esc(rel), esc(slug)))


# Abstract fallback key art: dark field, inner border, radial glow + twin
# orbit rings (hub/base motif) + a faint diagonal hairline pair. No text at
# all — the page h1 already names the project, and rendering the name twice
# (once in the h1, once in the banner) dominated the page. Decorative only.
FALLBACK_HERO = (
    '<div class="hero-art fallback" aria-hidden="true">'
    '<svg viewBox="0 0 1680 720" preserveAspectRatio="xMidYMid slice">'
    '<defs><radialGradient id="khg" cx="50%" cy="44%" r="68%">'
    '<stop offset="0%" stop-color="#1c2434"/>'
    '<stop offset="58%" stop-color="#10141d"/>'
    '<stop offset="100%" stop-color="#0b0e14"/>'
    '</radialGradient></defs>'
    '<rect width="1680" height="720" fill="url(#khg)"/>'
    '<line x1="140" y1="596" x2="1540" y2="124" stroke="#232b3a" stroke-width="1" opacity="0.55"/>'
    '<line x1="140" y1="620" x2="1540" y2="148" stroke="#1a2130" stroke-width="1" opacity="0.55"/>'
    '<circle cx="840" cy="318" r="92" fill="none" stroke="#33405a" stroke-width="2" opacity="0.85"/>'
    '<circle cx="840" cy="318" r="134" fill="none" stroke="#232b3a" stroke-width="1" opacity="0.6"/>'
    '<circle cx="932" cy="226" r="7" fill="#3a4a66" opacity="0.9"/>'
    '<rect x="36" y="36" width="1608" height="648" fill="none" stroke="#232b3a" stroke-width="3"/>'
    '</svg></div>'
)


def key_art_hero(slug, name, rel_prefix):
    """Key-art hero: generated art when the media pipeline produced it,
    otherwise the abstract FALLBACK_HERO banner in the key-art language
    (dark field, inner border — no text, the h1 names the project).
    Decorative (aria-hidden).

    rel_prefix points from the page back to the repo root."""
    hits = sorted(glob.glob(os.path.join(GEN_DIR, slug, "hero.*")))
    if hits:
        return hero_art(slug, rel_prefix)
    return FALLBACK_HERO


def module_hero(slug, name):
    """Key-art hero for a Castle module page (3 levels deep)."""
    return key_art_hero(slug, name, "../../../")


def project_hero(slug, name):
    """Key-art hero for a repo hub project page (2 levels deep) — the same
    treatment module pages already get, so hero-less projects (no generated
    art yet) still open with a banner instead of bare text."""
    return key_art_hero(slug, name, "../../")


def shots_exist(slug):
    """True when the media pipeline produced real screenshots for this slug.

    When false, the page's key-art hero already serves as the title card,
    so the Slideshow/Pictures section would just repeat the same banner
    twice — callers skip the section instead."""
    return bool(glob.glob(os.path.join(SHOTS_DIR, slug, "*.png")) +
                glob.glob(os.path.join(SHOTS_DIR, slug, "*.webp")))


def slideshow(slug, name, rel_prefix="../../", site=None):
    files = sorted(glob.glob(os.path.join(SHOTS_DIR, slug, "*.png")) +
                   glob.glob(os.path.join(SHOTS_DIR, slug, "*.webp")))
    caps = CAPTIONS.get(slug, DEFAULT_CAPTIONS)
    if not files:
        # Status-blind fallback: never hint at whether a site exists.
        return title_card(name, "DOGS")
    slides = []
    for i, f in enumerate(files):
        cap = caps[i] if i < len(caps) else "Screenshot"
        rel = "%sassets/shots/%s/%s" % (rel_prefix, slug, os.path.basename(f))
        slides.append(
            '<figure class="slide" data-cap="%s"><img src="%s" alt="%s" loading="lazy" decoding="async"></figure>'
            % (esc(cap), esc(rel), esc(cap))
        )
    dots = "".join(
        '<button class="sdot" data-i="%d" aria-label="Slide %d"></button>' % (i, i + 1)
        for i in range(len(slides))
    )
    # audit: one footer row (caption + dots + count) instead of a stacked
    # caption bar plus a separate dots bar; the count pill leaves the image
    # corner; arrows center on the image via .stage, not on the whole box.
    # .scap carries the first slide's caption so no-JS still reads it; JS
    # swaps it per slide from data-cap (aria-live announces the change).
    # .spause is the visible pause/play affordance for the autoplay drift —
    # hidden until JS confirms autoplay is live (.slides.live), so no-JS
    # visitors never see a dead control.
    return (
        '<div class="slides" data-slides="%d">'
        '<div class="stage"><div class="track">%s</div>'
        '<button class="snav prev" aria-label="Previous slide">‹</button>'
        '<button class="snav next" aria-label="Next slide">›</button></div>'
        '<div class="sfoot"><span class="scap" aria-live="polite">%s</span>'
        '<div class="sdots">%s</div>'
        '<div class="scount"><span class="cur">1</span> / %d</div>'
        '<button class="spause" aria-pressed="false" aria-label="Pause slideshow">❚❚</button></div>'
        '</div>' % (len(slides), "".join(slides), esc(caps[0] if caps else "Screenshot"), dots, len(slides))
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
.kicker{position:sticky;top:0;z-index:60;margin:-26px -18px 16px;padding:12px 18px;
  background:rgba(0,0,0,.84);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);
  border-bottom:1px solid var(--line);
  font-family:var(--display);letter-spacing:.28em;font-size:.78rem;color:var(--muted);
  text-transform:uppercase}
.kicker a{color:var(--muted);text-decoration:none;border-bottom:1px solid var(--line)}
.dossier-head h1{font-family:var(--display);font-size:clamp(2.6rem,9vw,5rem);
  text-transform:uppercase;letter-spacing:.02em;line-height:.95;margin-bottom:12px;
  overflow-wrap:break-word}/* long dot-names (yeahdogs.github.io) must wrap, not stretch 390px */
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
main>*{min-width:0}/* grid items must shrink: slideshow track's 3x intrinsic width was stretching pages (paper, yeahdogs.github.io overflowed 390px) */
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:22px}
.panel>h2{font-family:var(--display);font-size:1.35rem;letter-spacing:.12em;text-transform:uppercase;
  border-left:6px solid var(--text);padding-left:12px;margin-bottom:16px}
/* key art hero */
.hero-art{margin:0 0 6px;border-radius:12px;overflow:hidden;border:1px solid var(--line)}
.hero-art img,.hero-art svg{width:100%;display:block;aspect-ratio:21/9;object-fit:cover}
.hero-art.fallback svg{aspect-ratio:32/9}/* fallback has no art to show: a low banner, not a dominant box */
/* slideshow */
.slides{overflow:hidden;border-radius:8px;border:1px solid var(--line);background:#000}
.stage{position:relative;overflow:hidden}/* arrows center on the image itself, not on the whole box */
.track{display:flex;transition:transform .35s ease}
.slide{min-width:100%}
.slide img{width:100%;display:block;aspect-ratio:16/10;object-fit:cover;object-position:top}
.slide.titlecard svg{width:100%;display:block}
.snav{position:absolute;top:50%;transform:translateY(-50%);background:rgba(0,0,0,.65);color:#fff;border:1px solid var(--line);
  width:42px;height:42px;border-radius:50%;font-size:1.4rem;cursor:pointer;line-height:1}
.snav.prev{left:10px}.snav.next{right:10px}
/* touch-only layouts: swipe owns navigation, so the arrow chrome fades back
   over the key art and only surfaces when the visitor touches or focuses it */
@media (pointer:coarse){
  .snav{opacity:.55;transition:opacity .2s ease}
  .snav:hover,.snav:active,.snav:focus-visible{opacity:1}
}
/* slideshow footer: one row — caption left, dots + count right
   (audit: was a stacked caption bar plus a separate dots bar, 69px of chrome) */
.sfoot{display:flex;align-items:center;gap:8px 14px;flex-wrap:wrap;padding:10px 14px;border-top:1px solid var(--line)}
.scap{font-size:.78rem;color:var(--muted);flex:1 1 140px;min-width:0}
.sdots{display:flex;gap:2px;margin-left:auto}
.sdot{position:relative;width:30px;height:30px;border:0;background:transparent;cursor:pointer;padding:0}/* 30px tap target, 10px visual dot */
.sdot::after{content:"";position:absolute;inset:10px;border-radius:50%;border:1px solid var(--dim)}
.sdot[aria-current="true"]::after{background:var(--text);border-color:var(--text)}
.scount{font-size:.75rem;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
/* slideshow autoplay pause/play affordance: hidden until JS confirms autoplay
   is actually running (.slides.live) — no-JS and reduced-motion never show it */
.spause{display:none;width:30px;height:30px;border:1px solid var(--line);border-radius:50%;
  background:transparent;color:var(--muted);font-size:.6rem;cursor:pointer;line-height:1;
  transition:border-color .15s ease,color .15s ease}
.slides.live .spause{display:inline-flex;align-items:center;justify-content:center}
.spause:hover{border-color:var(--text);color:var(--text)}
.spause[aria-pressed="true"]{border-color:var(--text);color:var(--text)}
/* whats going on — commit box: kicker up top, message in mono */
.commit{background:#000;border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-top:12px}
.commit .ckicker{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin:0 0 8px;font-size:.68rem;letter-spacing:.14em;text-transform:uppercase;color:var(--dim)}
.commit .ckicker time{color:var(--muted);letter-spacing:.05em;font-variant-numeric:tabular-nums}
.commit .msg{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-weight:600;font-size:.92rem;line-height:1.55;margin:0 0 2px;overflow-wrap:break-word}
.commit .msg:last-child{margin-bottom:0}
.commit .msg .cdate{font-weight:400;color:var(--dim);font-size:.8rem}
/* status facts */
.facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.fact{border:1px solid var(--line);border-radius:10px;padding:12px;background:#000;min-width:0}
.fact .k{font-size:.68rem;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);margin-bottom:6px}
.fact .v{font-weight:600;font-size:.95rem;font-variant-numeric:tabular-nums;overflow-wrap:break-word;line-height:1.4}
.fact .v.ok{color:var(--green)} .fact .v.bad{color:var(--red)}
/* percent: the valued state gets the premium treatment — slim track, the fill settles in on reveal */
@keyframes pctfill{from{width:0}}
.pct-bar{height:10px;border:1px solid var(--line);border-radius:999px;overflow:hidden;background:#000;margin-bottom:12px}
.pct-bar i{display:block;height:100%;background:var(--text);animation:pctfill 1.1s cubic-bezier(.2,.7,.2,1) .25s backwards}
.pct-row{display:flex;justify-content:space-between;align-items:baseline;gap:10px;font-size:.85rem;color:var(--muted)}
.pct-row b{font-family:var(--display);font-size:1.35rem;letter-spacing:.06em;color:var(--text);font-variant-numeric:tabular-nums}
.inbuild{display:inline-block;font-family:var(--display);letter-spacing:.18em;font-size:1.1rem;
  border:2px solid var(--text);border-radius:8px;padding:10px 20px;margin-bottom:10px}
.fine{font-size:.85rem;color:var(--muted);line-height:1.5;max-width:60ch}
/* percent: the no-estimate state speaks the same dashed placeholder-card language as empty IDEAS/timeline panels */
.pct-empty{border:1px dashed var(--line);border-radius:10px;padding:20px 18px;max-width:52ch}
.pct-empty .inbuild{margin-bottom:12px}
.pct-empty .fine{margin:0}
/* ideas */
.ideas{list-style:none;display:grid;gap:10px}
.ideas li{border-left:3px solid var(--text);padding:8px 0 8px 14px;line-height:1.5}
.ideas .src{display:block;font-size:.72rem;color:var(--dim);margin-top:4px;letter-spacing:.06em}
.empty-note{color:var(--muted);line-height:1.6;border:1px dashed var(--line);border-radius:10px;padding:20px 18px;max-width:52ch}/* empty IDEAS/timeline panels read as an intentional placeholder card, not a forgotten blank */
.empty-note a{color:var(--text);text-decoration:underline;text-underline-offset:3px;text-decoration-color:var(--dim)}
.empty-note a:hover{text-decoration-color:var(--text)}
/* timeline */
.timeline{list-style:none;position:relative;padding-left:26px}
.timeline::before{content:"";position:absolute;left:8px;top:6px;bottom:6px;width:2px;background:var(--line)}
.timeline li{position:relative;padding:0 0 18px}
.timeline li::before{content:"";position:absolute;left:-24px;top:5px;width:12px;height:12px;
  border-radius:50%;background:var(--bg);border:2px solid var(--text)}
.timeline li.first::before{background:var(--text)}
.timeline .t{font-size:.75rem;color:var(--dim);letter-spacing:.08em}
.timeline .s{font-weight:600;line-height:1.45;margin-top:2px;overflow-wrap:break-word}
/* unbreakable tokens (window.Paper, slash-paths) must wrap inside 390px */
.timeline .tag{display:inline-block;font-family:var(--display);font-size:.68rem;letter-spacing:.16em;
  border:1px solid var(--text);border-radius:4px;padding:2px 8px;margin-left:8px;vertical-align:middle}
/* module cards */
.mods{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.mod{border:1px solid var(--line);border-radius:10px;background:#000;text-decoration:none;overflow:hidden;
/* 10px radius matches the fact/panel/placeholder card language (was 8px) */
  display:block;transition:border-color .15s ease,transform .15s ease}
/* image on top: each module card carries its own title-card thumb (aria-hidden,
   the link's h3 names the module) so no card is blank and no art repeats */
.mod:hover{border-color:var(--text);transform:translateY(-3px)}
.mod .mthumb{display:block}
.mod .mthumb svg{display:block;width:100%;height:auto}
.mod .mbody{display:block;padding:18px}
.mod h3{font-family:var(--display);letter-spacing:.06em;text-transform:uppercase;font-size:1.15rem;margin-bottom:8px}
.mod p{color:var(--muted);font-size:.88rem;line-height:1.5}
.mod .go{display:inline-block;margin-top:12px;font-size:.78rem;letter-spacing:.14em;
  font-family:var(--display);text-transform:uppercase;border-bottom:1px solid var(--text)}
/* motion: header entrance, scroll-reveal sections, staggered module cards */
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:translateY(0)}}
/* dossier-head: the head itself doesn't move — its children stagger in
   (h1 → lede → badges → actions), the same staggered language as the module cards */
.dossier-head>*{animation:rise .45s ease backwards}
.dossier-head .lede{animation-delay:.07s}
.dossier-head .badges{animation-delay:.14s}
.dossier-head .actions{animation-delay:.21s}
.reveal{opacity:0;transform:translateY(18px);transition:opacity .55s ease,transform .55s ease}
.reveal.in{opacity:1;transform:none}
.cardin{animation:rise .45s ease backwards}
/* key-art hero entrance: begins just as the dossier-head stagger resolves (~.66s),
   settles last — head first, art second */
@keyframes heroIn{from{opacity:0;transform:scale(1.06)}to{opacity:1;transform:scale(1)}}
.hero-art{animation:heroIn .9s ease .5s both}
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
  .dossier-head>*{animation:none}
  .pct-bar i{animation:none}/* reduced-motion: bar renders at final width, no fill-in */
  .reveal{opacity:1;transform:none;transition:none}
  .spause{display:none!important}/* autoplay is dead under reduced-motion: no affordance */
  .cardin{animation:none}
  .hero-art{animation:none}
  .mod:hover{transform:none}
  .skip{transition:none}
}
/* keyboard focus: visible ring on links, slideshow controls, cards — matches the dark aesthetic */
:focus-visible{outline:2px solid var(--green);outline-offset:3px;border-radius:6px}
/* skip-to-content: invisible until keyboard focus, then slides into view */
.skip{position:absolute;left:12px;top:-60px;z-index:200;background:var(--green);color:#000;
  font-weight:700;text-decoration:none;padding:10px 16px;border-radius:8px;transition:top .18s ease}
.skip:focus-visible{top:12px}
@media (max-width:560px){
  body{padding:18px 12px 32px}
  .kicker{margin:-18px -12px 14px;padding:12px 12px;letter-spacing:.18em}
  .panel{padding:16px}
  .hero-art{margin-bottom:14px}/* 390px: hero gets breathing room above the slideshow panel */
  .hero-art img,.hero-art svg{aspect-ratio:16/10}/* 390px: 21/9 reads as a thin sliver — 16/10 keeps the key art visible */
  .hero-art.fallback svg{aspect-ratio:21/9}/* 390px: fallback stays a low banner, not a dominant box */
  .slide img{aspect-ratio:4/5;object-position:top}
  .snav{width:36px;height:36px;font-size:1.2rem}
  .snav.prev{left:6px}.snav.next{right:6px}
  .sdot{width:26px;height:26px}
  .sdot::after{inset:9px}
  .timeline .tag{display:block;width:fit-content;margin:8px 0 0;vertical-align:baseline}/* 390px: tag gets its own line so it never crowds the subject */
  .dogs-foot img{max-width:180px}
}
@media (min-width:900px){
  .ideas{grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px 22px}/* desktop: two columns so the IDEAS panel fills the 1080px content width instead of one skinny column */
  .timeline li{display:grid;grid-template-columns:110px 1fr;gap:4px 22px;align-items:baseline}/* desktop: date and subject share one row — the TIMELINE panel fills the 1080px content width instead of stacking like mobile */
  .timeline .t{text-align:right}
  .timeline .s{margin-top:0}
}
"""

JS = """
(function(){
  document.querySelectorAll('.slides[data-slides]').forEach(function(box){
    var n=+box.getAttribute('data-slides'), i=0;
    var track=box.querySelector('.track'), cur=box.querySelector('.cur'), scap=box.querySelector('.scap');
    var dots=Array.prototype.slice.call(box.querySelectorAll('.sdot'));
    function go(k){ i=(k+n)%n; track.style.transform='translateX(-'+(i*100)+'%)';
      cur.textContent=i+1;
      if(scap){ var c=track.children[i]&&track.children[i].getAttribute('data-cap'); if(c) scap.textContent=c; }
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
    // handed to the visitor on their first manual interaction. The .spause
    // button is the visible pause/play affordance: it only appears
    // (.slides.live) when autoplay can actually run (n>1, no reduced-motion).
    var reduceA = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var timer=null, owned=true;
    var pauseBtn=box.querySelector('.spause');
    function renderPause(){ if(!pauseBtn) return;
      var playing = owned && !reduceA && n>1;
      pauseBtn.setAttribute('aria-pressed', playing?'false':'true');
      pauseBtn.setAttribute('aria-label', playing?'Pause slideshow':'Play slideshow');
      pauseBtn.textContent = playing?'❚❚':'▶'; }
    function start(){ if(!owned||timer||reduceA||n<2) return;
      timer=setInterval(function(){ if(!document.hidden) go(i+1); }, 6000); }
    function stop(){ if(timer){ clearInterval(timer); timer=null; } }
    function takeover(){ owned=false; stop(); renderPause(); }
    if(pauseBtn){ pauseBtn.addEventListener('click', function(){
      owned=!owned; if(owned){ start(); } else { stop(); } renderPause(); }); }
    if(!reduceA && n>1){ box.classList.add('live'); }
    start();
    renderPause();
    box.addEventListener('pointerenter', stop);
    box.addEventListener('pointerleave', start);
    document.addEventListener('visibilitychange', function(){
      if(document.hidden) stop(); else start(); });
    start();
    go(0);
  });

  // motion: scroll-reveal sections + staggered module/fact-card entrance.
  // classes are added from JS only, so no-JS visitors never see hidden content.
  // audit fix: the card stagger used to fire at page load, so cards in panels
  // below the fold (e.g. castle's Modules) animated invisibly behind the panel's
  // own opacity:0 reveal. The stagger now fires per-panel, when the panel is
  // actually scrolled into view.
  (function(){
    var reduceM = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var hasIO = 'IntersectionObserver' in window;
    function cardinPanel(scope){
      var cards = scope.querySelectorAll('.mod, .fact');
      Array.prototype.forEach.call(cards, function(c, i){
        c.style.animationDelay = Math.min(i*45, 700) + 'ms';
        c.classList.add('cardin');
      });
    }
    if(!reduceM && hasIO){
      var io = new IntersectionObserver(function(es){
        es.forEach(function(en){
          if(en.isIntersecting){
            en.target.classList.add('in');
            cardinPanel(en.target);
            io.unobserve(en.target);
          }
        });
      }, {rootMargin:'0px 0px -6% 0px', threshold:0.06});
      Array.prototype.forEach.call(document.querySelectorAll('main .panel'), function(s){
        s.classList.add('reveal'); io.observe(s);
      });
    } else {
      // reduced-motion or no IntersectionObserver: no scroll-reveal, so cards
      // enter at load exactly as before (CSS still neuters the animation when
      // reduced-motion is on).
      cardinPanel(document);
    }
  })();
})();
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — DOGS</title>
<meta name="description" content="{meta_desc}">
<link rel="icon" type="image/svg+xml" href="{favicon}">
<link rel="manifest" href="{manifest}">
<link rel="apple-touch-icon" href="{apple_touch_icon}">
<meta name="theme-color" content="#0b0e14">
{social}
{jsonld}
<style>{css}</style>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<p class="kicker"><a href="{home}">Tower</a>{crumb}</p>
<header class="dossier-head">
  <h1>{name}</h1>
  <p class="lede">{lede}</p>
  {actions_row}
</header>
{hero}
<main id="main">
{sections}
</main>
<footer class="dogs-foot">
  <a href="https://wearedogs.net"><img src="{badge_src}" alt="DOGS" decoding="async"></a>
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

def actions_row(buttons):
    """The header actions row, or "" when a page has no buttons.

    Most dossier pages have no outbound links at all now (status-blind):
    only module pages keep their back-to-hub link."""
    if not buttons:
        return ""
    return '  <div class="actions">%s</div>\n' % buttons


def repo_hub(repo, meta, pitches):
    """Investor-facing project page: status-blind by construction.

    Name, one pitch line, key art, product screenshots, demo videos.
    No badges, no outbound links, no repo stats, no commit history —
    nothing that distinguishes an online project from an offline one."""
    name = repo["name"]
    pitch = pitch_for(pitches, name, repo.get("description") or "")

    sections = "\n".join(filter(None, [
        section("Screenshots", slideshow(name, name))
        if shots_exist(name) else None,
        videos_section([repo]),
    ]))
    title_full = "%s \u2014 DOGS" % name
    desc = pitch[:160]
    return PAGE.format(
        title=esc(name), meta_desc=esc(desc),
        social=social_meta(title_full, desc, "projects/%s/" % name, name),
        jsonld=json_ld(title_full, desc, "projects/%s/" % name),
        favicon=FAVICON,
        manifest=MANIFEST,
        apple_touch_icon=APPLE_TOUCH_ICON,
        css=CSS, home="../../", crumb=" / " + esc(name), name=esc(name),
        lede=esc(pitch),
        actions_row=actions_row(""),
        hero=project_hero(name, name),
        sections=sections, badge_src=BADGE, js=JS,
    )


def group_hub(slug, group, repos_by_name, meta, pitches):
    """Investor-facing group page (Castle): status-blind like repo_hub.

    Pitch line, key art, screenshots, demo videos, and the module grid.
    No badges, no repo buttons, no commit history."""
    members = [repos_by_name[m] for m in group.get("members", []) if m in repos_by_name]
    pitch = pitch_for(pitches, slug, group.get("tagline") or "")

    mods = []
    for mod in group.get("modules", []):
        mods.append(
            '<a class="mod" href="%s/">%s<span class="mbody"><h3>%s</h3><p>%s</p>'
            '<span class="go">Open dossier \u2192</span></span></a>'
            % (esc(mod["slug"]), mod_thumb(mod["title"]), esc(mod["title"]),
               esc(mod.get("blurb") or "")))
    mods_html = '<div class="mods">' + "".join(mods) + "</div>"

    sections = "\n".join(filter(None, [
        section("Screenshots", slideshow("castle", group["title"]))
        if shots_exist("castle") else None,
        videos_section(members),
        section("Modules", mods_html),
    ]))
    title_full = "%s \u2014 DOGS" % group["title"]
    desc = pitch[:160]
    return PAGE.format(
        title=esc(group["title"]),
        meta_desc=esc(desc),
        social=social_meta(title_full, desc, "projects/%s/" % slug, slug),
        jsonld=json_ld(title_full, desc, "projects/%s/" % slug),
        css=CSS, home="../../", crumb=" / " + esc(group["title"]),
        favicon=FAVICON,
        manifest=MANIFEST,
        apple_touch_icon=APPLE_TOUCH_ICON,
        name=esc(group["title"]),
        lede=esc(pitch),
        actions_row=actions_row(""),
        hero=hero_art(slug),
        sections=sections, badge_src=BADGE, js=JS,
    )


def module_page(group_slug, group, mod, meta, pitches):
    """Investor-facing module page: status-blind. Blurb, key art,
    screenshots, demo videos, plus a sibling-modules strip (same treatment
    as the hub's Modules grid) so a module with no shots or videos still
    has a body instead of an empty page. Keeps only the back-to-hub link."""
    shot_slug = "%s-%s" % (group_slug, mod["slug"])

    vids = mod.get("videos") or []
    vids_html = ""
    if vids:
        vids_html = '<div class="vids">' + "".join(
            '<video controls preload="metadata" src="%s"></video>' % esc(v) for v in vids) + "</div>"

    sibs = [m for m in group.get("modules", []) if m["slug"] != mod["slug"]]
    sibs_html = ""
    if sibs:
        sibs_html = '<div class="mods">' + "".join(
            '<a class="mod" href="../%s/">%s<span class="mbody"><h3>%s</h3><p>%s</p>'
            '<span class="go">Open dossier \u2192</span></span></a>'
            % (esc(m["slug"]), mod_thumb(m["title"]), esc(m["title"]),
               esc(m.get("blurb") or ""))
            for m in sibs) + "</div>"

    sections_list = []
    if shots_exist(shot_slug):
        sections_list.append(section("Screenshots", slideshow(shot_slug, mod["title"], "../../../")))
    if vids_html:
        sections_list.append(section("Videos", vids_html))
    if sibs_html:
        sections_list.append(section("More from %s" % group["title"], sibs_html))

    title_full = "%s \u2014 DOGS" % mod["title"]
    desc = (mod.get("blurb") or "")[:160]
    share = share_slug_for(shot_slug, mod["slug"], group_slug)
    return PAGE.format(
        title=esc(mod["title"]),
        meta_desc=esc(desc),
        social=social_meta(title_full, desc,
                           "projects/%s/%s/" % (group_slug, mod["slug"]), share),
        jsonld=json_ld(title_full, desc,
                       "projects/%s/%s/" % (group_slug, mod["slug"])),
        css=CSS, home="../../../",
        favicon=FAVICON,
        manifest=MANIFEST,
        apple_touch_icon=APPLE_TOUCH_ICON,
        crumb=' / <a href="../" style="color:var(--muted)">%s</a> / %s'
              % (esc(group["title"]), esc(mod["title"])),
        name=esc(mod["title"]), lede=esc(mod.get("blurb") or ""),
        actions_row=actions_row('<a class="btn ghost" href="../">\u2190 Castle hub</a>'),
        hero=module_hero(mod["slug"], mod["title"]),
        sections="\n".join(sections_list), badge_src="../../../assets/made-by-dogs.webp", js=JS,
    )


# ---------------------------------------------------------------- main

def main():
    with open(DATA_JSON) as f:
        data = json.load(f)
    with open(GROUPS_JSON) as f:
        groups = json.load(f).get("groups", {})
    with open(META_JSON) as f:
        meta = json.load(f)
    pitches = load_pitches()

    repos = data["repos"]
    repos_by_name = {r["name"]: r for r in repos}
    grouped_members = set()
    for g in groups.values():
        grouped_members.update(g.get("members", []))

    # repo hubs (ungrouped repos only)
    for r in repos:
        if r["name"] in grouped_members:
            continue
        write_page(os.path.join(PROJECTS_DIR, r["name"], "index.html"), repo_hub(r, meta, pitches))

    # group hubs + module sub-pages
    for slug, group in groups.items():
        write_page(os.path.join(PROJECTS_DIR, slug, "index.html"),
                   group_hub(slug, group, repos_by_name, meta, pitches))
        for mod in group.get("modules", []):
            write_page(os.path.join(PROJECTS_DIR, slug, mod["slug"], "index.html"),
                       module_page(slug, group, mod, meta, pitches))

    print("hub build complete.")


if __name__ == "__main__":
    main()
