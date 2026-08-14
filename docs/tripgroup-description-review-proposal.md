# TripGroup description review — proposal (2026-08-14)

Family-level trip descriptions (stored on the **TripGroup** doc, EN + target language)
get their own two-stage review flow, separate from trip sessions. Context: the per-trip
`tripgroup_description` field was removed from seeding the same day (it was redundant —
and its approve path 404'd on leveled rungs; description edits inside a trip session now
write only the Trip doc's own `descriptionTarget`).

## Shape of the work

One review item per **family** (= TripGroup doc), three stages:

```
pending_en ──(admin approves EN desc + categories)──► translating
translating ──(auto MT, EN→TL)──► pending_tl
pending_tl ──(translator approves TL text)──► done
```

Scotland/UK (target = EN) skip straight from EN approval to `done` — the approved EN
text is written as both home and target.

## Storage

New SQLite table `tripgroup_reviews` — NOT a session (no audio, no scenes, no coverage):

```
tg_id TEXT PK · language TEXT · status TEXT (pending_en|translating|pending_tl|done)
en_text TEXT · en_original TEXT · tl_text TEXT · tl_original TEXT
categories_json TEXT · en_by/en_at · tl_by/tl_at · created_at
```

Queue population (decided): **auto-populate from every family in the task list** —
every TripGroup referenced by a family in `trips_to_review.json` (plus completed
trips), seeded lazily on first list. A `scripts/` backfill command seeds the
historical set.

TripGroup fields (confirmed): `descriptionHome` (EN), `descriptionTarget` (TL),
`tripCategories`, plus the derived `tooltip` (see below).

## Stage A — admin / English check (`pending_en`)

New admin page ("Trip descriptions" nav item, badge = count pending). Per family it
shows, read-only for context, pulled from the family's **`_EN` trip doc** live:

- scene thumbnails (existing `thumbs.py` resolution), `titleKey`, `SceneDesc` per scene
- current `tripCategories` from the TripGroup
- the EN description (editable textarea)

Plus a category editor: confirm / remove existing, add from the controlled vocabulary
(reuse `routes_admin.py`'s vocabulary source + `get_enrichment_categories` suggestions).
The task: verify the description is accurate and covers the most important places.

**Approve (EN)** writes to the TripGroup doc: `descriptionHome` + `tripCategories`
(+ regenerated `tooltip`, see below). Then status → `translating` (or `done` for
EN-target families, also writing `descriptionTarget`).

### Derived `tooltip` (kept in sync on EVERY description write)

Mirrors `Scripts\TripGroupDescFromTrip-Select.py`: `tooltip` = first sentence
(up to and including the first `.`; whole text if none) of `descriptionTarget`,
then `\n\n`, then first sentence of `descriptionHome` — single part if one is
empty. Port the two small helpers into the app (they're 15 lines, and the Scripts
file isn't importable-safe); regenerate the tooltip whenever stage A or stage C
writes either description.

## Stage B — machine translation (`translating`)

Decided: **the `claude` CLI** (as Gate-2 does — dave has a large token allowance;
no direct API). Consistency (identical translated phrasing for "Guide:", "Trip Type:",
"Approx duration:" across all families) is met deterministically, template-first:

1. **One-off extraction**: script reads all live TripGroups per language and derives the
   canonical boilerplate lines (guide line, Trip Type label + category translations,
   duration phrasing) into a committed per-language phrase table
   (`backend/app/data/tripdesc_phrases.json`). Category names translate via this table,
   not the model.
2. **Split the description**: boilerplate lines rendered from the table; only the free
   prose goes to the model.
3. **`claude` CLI call** (Sonnet) translates the prose, with 2–3 existing live TL
   descriptions from the same language in the prompt as style anchors. Reuse Gate-2's
   hardened `call_claude` (real-reason surfacing from the stdout JSON envelope) so a
   lapsed CLI login is loud, not a silent retry loop.
4. Failure is non-fatal: item stays `translating` with the error surfaced on the admin
   page + a retry button; never blocks other items.

Result stored as `tl_text`, status → `pending_tl`.

## Stage C — translator review (`pending_tl`)

Appears in that language's reviewer trip list as a lightweight card
("Trip description — Monaco2"), gated by the existing language ACL. Opening it shows
ONLY: the TL text (editable) and the approved EN text (reference). No scenes, thumbs,
categories, or audio. Approve writes `descriptionTarget` (+ regenerated `tooltip`) to the
TripGroup, status → `done`. Reviewer-facing notification reuses the nav badge + `activity_notifier` pattern.

## Interactions / edge cases

- **Re-check**: admin can reopen a `done` item (status back to `pending_en`); TL is
  re-translated only if the EN prose changed.
- **Leveled rungs**: the flow is per-family, so `Monaco2_A12_FR`/`_B1_FR`/native all
  resolve to the ONE family TripGroup — need the rung→family mapping (the
  `source.json` family model / `review_block.native_tl_id` logic, or a `TripGroups`
  `trips[]` reverse lookup as in `routes_admin.py:75`).
- Trip sessions no longer touch the TripGroup at all — no write conflicts between the
  two flows.

## Status (2026-08-14): BUILT

Implemented same day: `tripgroup_reviews` table (db.py), `backend/app/tripdesc.py`
(seeding, tooltip derivation, stage transitions, claude-CLI translation on a
background thread), `routes_tripdesc.py` (`/api/tripdesc*`), FE pages
`/descriptions` + `/descriptions/:tgId` and the nav "Descriptions" badge.
Tests: `backend/tests/test_tripdesc.py`. Historical backfill (dave confirmed
2026-08-14): ONLY Jedburgh1_TownAbbey, Jedburgh2_CastleJail, HadriansWall, Melrose,
Abbotsford — `backend/scripts/backfill_tripdesc.py`, to be run once on the laptop;
the other 19 no-longer-in-manifest completed families were explicitly rejected.
NOT yet done: the per-language phrase-table extraction (translation currently anchors on live same-language
TripGroup exemplars in the prompt; the committed table remains an option if
consistency proves insufficient).
