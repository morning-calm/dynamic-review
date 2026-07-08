# review-app — 2026-07-09

## Red-team review of the direct scene-structure editor (96d6de8)

**Goal:** adversarial review of `backend/app/structure.py` + the new
`/api/admin/structure/*` routes + `StructureEditorPage.tsx`; fix real defects,
commit locally on main (no push, no deploy).

**What I did** (commit `690d3a3`):
- Made every structural op a **Firestore transaction**: re-read Trip, re-check
  `base` fingerprint inside the txn, write quickTrips + renumbered
  TripLocalizations atomically. Outer retry for read-time `Aborted` (the
  `@firestore.transactional` decorator only retries commit-time aborts —
  verified against the installed 2.21 source); persistent contention → 409.
- **sceneId assignment vs real data:** staged videoUrls are bare Vimeo ids, so
  the mp4-stem regex never fired → every add/rekey minted a duplicate atom.
  Now: registry lookup `videoIds array_contains vid` reuses the existing atom;
  else derive from the VideoIds filename stem (`725676693` →
  `s20220508-141520`, matches the real registry doc); else filename fallback;
  all mint/derive still only via the shared `scene_ids` ruleset.
- `_registry_add_use` no longer clobbers an existing atom's pinned
  `currentVideoId` (the `remap` docs) or `kind` (`flat` atoms exist).
- Requested `scene_id` reuse now 422s (`unknown_scene_id`) when the atom is
  not in the registry — no more phantom registry docs from typos.
- Registry updates post-commit + best-effort: failures land in `warnings` +
  the audit row (with `scene_id_how` provenance), never a 500 hiding an
  applied write.
- Post-commit detection + loud warning when a session was opened mid-op
  (guard race can't be fully prevented across SQLite/Firestore).
- `set_categories` 404s on a bogus trip BEFORE touching the TripGroup.
- FE: NaN/clamp guard on the add-position input; de-duplicated scene list key.
- API_CONTRACT.md add/swap-video rows updated.

**Verified:** `py_compile` + `import app.main` green; `npm run build` green.
Behavioral suite against STAGING with a fully synthetic disposable trip
(`Fable_RT_Structure_EN` + TripGroup + TripLocalizations + synthetic
`Scenes/s2099…` docs): 31 checks — reorder/remove/add loc renumbering, stale
base 409, threaded race (one ok / one 409), active-session 409,
unknown-scene-id 422, atom reuse by videoId, dup-atom usedBy retention, pinned
currentVideoId/kind preservation, swap rekey vs non-rekey registry semantics,
derive-from-stem, categories, bogus-trip 404, audit rows. 3× ALL PASS; every
synthetic staging doc and local structure_ops/session row deleted
(`leftovers: none` verified). Real staging trips touched READ-only.

**Noted, not fixed (deliberate/accepted):**
- Fingerprint doesn't cover videoUrl when a sceneId is present — a concurrent
  non-rekey swap-video isn't caught by `base` on an index-targeted op; identity
  is still correct, so left as designed.
- Same stem-derived id already in the trip gets `-b` via `ensure_unique`
  (mirrors `assign_scene_ids`); explicit same-atom reuse is via `scene_id`.
- Design intents left alone per owner decision: direct writes outside sessions,
  index-keyed TripLocalizations, positional media warning-only, verbatim
  categories.

**Next steps:** none pending for this feature; unpushed commits remain local
on main per instructions.
