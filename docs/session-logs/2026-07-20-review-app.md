# Session log — review-app — 2026-07-20

## Checkpoint 1 — native EU `_<LANG>` rungs admitted to the review queue (Monaco)

**Goal:** act on the dynamic-content handoff: the native TL rung (`Monaco1_FR` /
`Monaco2_FR`, the "Int/Adv" DeepL translation) had no supported path into the review
app — decide (a) admit it into the variant model vs (b) declare it out-of-scope, add
the Monaco trips, and document the mechanism (full `_A12`/`_B1`/native ladders are
coming for all EU languages).

**Decision: (a) — first-class, name-derived.** The native rung IS the family's B2+/Int-Adv
tier, so it belongs in the queue. Rather than inventing a `source.json` for a legacy
artifact, `review_block.native_tl_id` derives it from the family's `_Beg_<LANG>` source id
(`Monaco1_Beg_FR` → `Monaco1_FR`); `family_variants` includes it as a member, and
`audio_present` gates its `[review]` row like every other member — so listing is automatic
once the reviewed masters are canonical (local `Audio Generation/<cid>/` or R2
`review-audio/<cid>/`). Hand-added rows are no longer needed and rebuilds now EMIT the
rung instead of dropping it.

**What I did** (all Scripts-repo code; the review app needed NO changes — bare `_FR` ids
already resolve French / speed 1.0 / gendered voice in `audio_core`):
- `Trello/review_block.py`: `native_tl_id` + inverse `beg_source_for_native_tl`;
  `family_variants` adds the rung; `lane_for` defaults it to lane 7 (already
  translator-reviewed via the legacy flow — app work is a KP-style confirm), own-card
  lane still wins. Not derived for suffix-less Spanish `_Beg`/`_Int` natives (ambiguous)
  or native-sourced sids like `Cassis_Calanques_FR` (`is_native` covers those).
- `Trello/tripgroup_membership.py`: the rung returns `skip_native` (its TripGroup entry
  is owned by the legacy lane-4a/9 pipeline — never re-wired from 5c);
  `resolve_tripgroup_id` also tries the `_Beg` source's `_TRIPGROUP_ID_OVERRIDES` entry so
  the rung's voice resolves from its live TripGroup Guide line (`Monaco1_FR` → `Monaco1FR`
  → `sebastien`). **Fixed the EU guide-clobber collateral** the handoff flagged: a
  voiceless re-run used to rewrite an EU entry's Guide line with an EMPTY voice
  ("voice UNRESOLVED"); it now preserves the existing line.
- `upload_review_audio_r2.resolve_audio_dir`: added the two-level legacy tree
  `Audio Generation/0 Uploaded to check/<Language>/<cid>/` (handoff item 1 — why the
  native-FR audio looked "missing"; `review_block.audio_present` already globbed it).
- **Registered Monaco:** `sync_review_variant --variant Monaco{1,2}_A12_FR --apply`
  (blocks gained `Monaco{1,2}_FR lane=7`, voices resolved `sebastien`, TripGroups noop)
  → `export_review_trips.py` (manifest committed+pushed) → `git pull` on the live laptop.
- Docs: Scripts `CLAUDE.md` § 5a/5c new "EU family model" subsection (the if-this-happens-
  again runbook + full-ladder expectation); this repo's `CLAUDE.md` trip-list note.

**Verified**
- `py_compile` all three files; `uv run pytest tests/test_review_block_splice.py` — 8 passed.
- Helper sanity: `native_tl_id('Monaco1_Beg_FR')='Monaco1_FR'`, None for
  `Cassis_Calanques_FR`/`El_Planeta_Beg`/`Taichung_EN`; leveled ids never invert.
- Dry-runs before every write: backfill diff showed exactly the two new lane-7 rows
  (IT natives correctly excluded — no audio); sync dry-run showed TripGroup **noop** for
  the A12 entries (guide-clobber fix confirmed against the handoff's dry-run).
- Manifest on the laptop lists all 8 Monaco trips; `Monaco{1,2}_FR` lane 7, level FR,
  voice sebastien. R2 `review-audio/Monaco1_FR/` (36) + `Monaco2_FR/` (42) were already
  in place per the handoff, so laptop seeding works from the R2 fallback.
- audit_unlisted: unchanged 35 known items (handoff item 3 — production/pipeline noise);
  Monaco natives do NOT appear (they're listed). Handoff item 2's predicted noise is moot.

**Open / low-urgency TODOs**
- Manifest `family` for a native rung is itself (`Monaco1_FR`) while its drafts group
  under `Monaco1_Beg_FR` — cosmetic inconsistency in All-trips family grouping; revisit
  only if the filter UX ever groups by family.
- Monaco1/2_IT natives will auto-list the same way once their reviewed audio is placed
  canonically (currently no audio → excluded).
- B1 rungs for the EU ladder: nothing to code — suffixes/speeds already handled
  end-to-end; they flow when 5c produces them.

**Next steps:** none for the app. Future native rungs: place audio canonically → sync →
export (documented in Scripts CLAUDE.md § 5a/5c).

## Checkpoint 2 — Monaco to the top of the reviewer list

Dave wants Monaco 1+2 (all families) first. Reviewer order = manifest order =
(lane, **Trello card position**, trip_id); admin in-app **pin** floats above that
ad hoc. Moved the 4 Monaco family cards to the top of lane 6 via the Trello API
(order Monaco1 FR/IT, Monaco2 FR/IT), re-ran the export (manifest pushed), laptop
pulled. Verified: lane 6 opens Monaco1_A12_FR/IT, Monaco2_A12_FR/IT; lane 7 opens
Monaco1_Beg_FR, Monaco1_FR, Monaco2_Beg_FR, Monaco2_FR. Survives future re-exports
(drag-order is the durable mechanism; pins are per-trip overrides).
