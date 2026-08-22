# Social-post drafting (release-prep step)

Launch social posts (Meta + LinkedIn) and the in-app `news.json` entry are drafted
per **release batch** by a **Claude Sonnet (high effort) agent** at release-prep
time — never guessed from a place name, never auto-posted. Dave proofs and pastes.

## What drives it

- **Scope** = a release batch (Publisher → Publishing Queue → batch view /
  `GET /api/final/batches`). The batch's `resolved.trip_ids` is the exact trip
  list; the batch **name** is the release name.
- **Guidance** (all in the Scripts repo — the agent must read these, not improvise):
  - `Scripts/social/README.md` — naming, house style, the no-em-dash rule, and the
    hard rule to base every bullet on the **staging TripGroup descriptions** (and
    SceneDescs if needed).
  - `Scripts/docs/workflows/STAGE_10B.md` §7 (posts) + §7b (`news.json`: back up
    the current file to `Comms\OLD\news - <DD-mon-yy>.json` FIRST, new entry at
    the top, move the Discord footer, no em dashes).
  - Templates: `Scripts/social/templates/{meta,linkedin}_post_template.txt`;
    voice/format from `Scripts/social/templates/examples/`.
- **Output**: `D:\Dynamic Languages\Comms\Social Posts\<Release>_meta.txt` +
  `<Release>_linkedin.txt`, plus the updated `D:\Dynamic Languages\Comms\news.json`.
  The Publishing Queue's batch **social chip** (release_batches.social_state) turns
  ready when both files exist and news.json mentions the release.

## How to run it (any Claude session in this repo)

Spawn a subagent: **model sonnet, high effort**, with a prompt of this shape —

> Draft the launch social posts for the release batch "<NAME>" containing these
> trips: <resolved.trip_ids>. First read D:\Dynamic Languages\Scripts\social\README.md
> and docs/workflows/STAGE_10B.md §7–7b and follow them exactly. Read each family's
> staging TripGroup descriptionHome (backend .venv has Firestore access via
> backend/app/staging.py, or read the descriptions from the review app) — every
> bullet must describe only what the release actually contains. Write
> <Release>_meta.txt and <Release>_linkedin.txt to D:\Dynamic Languages\Comms\Social Posts\
> and update Comms\news.json per §7b (back it up to Comms\OLD first). No em dashes
> anywhere. Do not post anything.

Then verify: both files present, no `—` in any output, news.json backed up + valid
JSON + new entry on top, and the batch chip shows **ready**.
