# 2026-08-21 — review-app

## Tripdesc: production-existence seed guard + cleanup

**Goal:** stop the family-description queue seeding TripGroups that were already
checked (any group live on production Firebase has shipped ⇒ checked — dave's rule);
clean out the ~wrongly-seeded backlog. Constraint: the prod Firebase key must NOT
go to the Ubuntu laptop.

**What I did**
- `Scripts/Trello/export_review_trips.py` (dynamic-content): new
  `export_prod_tripgroups()` — streams PRODUCTION `TripGroups` ids (ids only,
  `select([])`) using the workstation-only `firebase_production_key2.json`, writes
  `prod_tripgroups.json` at this repo's root, committed/pushed with the manifest.
  Best-effort: key missing / auth failure / EMPTY listing keeps the previous
  snapshot (a wrongly-empty snapshot would over-seed; stale only over-seeds
  recently-published groups — safe direction).
- `backend/app/config.py`: `PROD_TRIPGROUPS_PATH`.
- `backend/app/tripdesc.py`: `seed_from_manifest` skips (with log line) any family
  whose tg_id is in the snapshot; returns `skipped_prod` count. Missing/unreadable
  snapshot degrades loudly to seed-everything (warn once). `seed_trips` /
  `backfill_tripdesc.py` deliberately ignore the snapshot — the "review it anyway"
  escape hatch.
- `backend/scripts/cleanup_tripdesc_prod.py`: deletes UNTOUCHED rows only
  (`pending_en` + `en_by IS NULL` + `en_text = en_original`) whose group is on
  prod; dry-run default, refuses without a snapshot.
- Commits: review-app `619336b` (code) + export commit (manifest + snapshot,
  320 prod ids); Scripts change left for dave to commit via GitHub Desktop.

**Verified**
- `pytest tests/test_tripdesc.py` → 16 passed (3 new: prod-skip, missing-snapshot
  degrade, backfill-ignores-snapshot).
- Export run: 320 prod TripGroup ids written.
- Deployed to laptop (git pull, `sudo -n systemctl restart review-app.service`);
  review-app + review-tunnel both active.
- Cleanup dry-run then `--apply` on live review.db: **168 rows deleted** (JP
  Tokyo/Shikoku, EU Beg families, all UK trips — exactly the wrongly-seeded class).
  31 rows remain: Kyoto batch, Monaco FR/IT, Tokyo_06-10, Hida/Takayama, Korean,
  Mandarin, + the 5 deliberately-backfilled Scotland families.

**Open / low-urgency**
- **Monaco1IT/Monaco2IT survived** — dave named Monaco IT as already-checked, but
  those groups are NOT in the prod snapshot, so by the agreed rule they stay
  pending_en (one redundant admin approval each, or approve/ignore). Flagged to dave.
- Scripts-side change uncommitted (dave commits dynamic-content via GitHub Desktop).
- Pre-existing export audit still lists many audio-ready drafts on no lane-6/7 card
  (Yokohama/Yamanakako/Xiangde/etc.) — untouched by this work.
