# Progress timelines — worker contract

Each YEAHDOGS project gets a dated progress timeline on the Tower dashboard.
`build.py` reads this directory; future workers (the octopus loop) append
entries here without touching code.

## File format

One file per repo: `progress/<repo>.json`
(repo name must match the YEAHDOGS org repo name exactly, e.g. `wax.json`)

```json
{
  "project": "wax",
  "entries": [
    {
      "date": "2026-09-09T14:32:00-05:00",
      "title": "Crate Digger verdict engine: Good deal / Fair / Overpriced",
      "kind": "commit",
      "ref": "a849548",
      "branch": "jack/wax-price-verdict",
      "url": "https://github.com/YEAHDOGS/wax/commit/a849548",
      "detail": "Grade-segmented verdict engine, ±15% bands, reissue guardrail. 368/368 tests green."
    }
  ]
}
```

## Entry fields

| Field    | Required | Notes |
|----------|----------|-------|
| `date`   | yes      | ISO 8601 with timezone offset, e.g. `2026-09-09T14:32:00-05:00`. **Real dates only** — use `git log --format=%ci` for commits, actual publish time for media. Never fabricate. |
| `title`  | yes      | Short human-readable label, ~140 chars max. |
| `kind`   | yes      | One of: `screenshot`, `video`, `doc`, `note`, `deploy`, `commit`. |
| `url`    | no       | Deep link: commit URL, doc URL, or path relative to the dashboard root (e.g. `demos/01-wax-crate-digger.mp4`). Omit when the work isn't pushed anywhere. |
| `thumb`  | no       | Relative image path shown as the thumbnail for `screenshot` entries (falls back to `url`). |
| `detail` | no       | One or two sentences of context — test counts, what changed, why it matters. |
| `ref`    | no       | Commit SHA (short) or version string. |
| `branch` | no       | Branch the work lives on. |

## Rules

- Entries may be appended in any order; `build.py` sorts newest-first and keeps
  the newest 40 per project.
- Entries missing `date`/`title`/`kind`, with an unknown `kind`, or with an
  unparseable `date` are dropped with a warning — nothing crashes the build.
- This directory ships on the branch it was created on; it is public data.
  Keep it free of secrets, tokens, emails, and personal data — the PII guard in
  `build.py` runs over the final payload.
- For `screenshot`/`video` entries, prefer committing the media file into the
  repo (e.g. `demos/`) and linking it relatively, so the timeline works on
  GitHub Pages without external hosts.
