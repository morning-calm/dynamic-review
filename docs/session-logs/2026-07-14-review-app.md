# Session log — review-app — 2026-07-14

## Goals
A batch of reviewer-reported bugs + new features (dave), built together, red-teamed in
one sweep, then committed/pushed/deployed to the live Ubuntu laptop:
1. 360-image thumb should use the 4K version, not the 15 MB master.
2. Adding silence to question audio (not just narration).
3. "You can see many people." (Bath A2) — audio cut off the end of the sentence;
   something wrong in the ElevenLabs submission.
4. Security: is the R2/S3 rename/upload path safe? Did any user gain staging write?
5. Insert/trim silence is very slow.
6. Add a 3-second-silence button.
7. "Edit waveform" button — reveal the waveform with precise splice/silence controls.
8. Top header should stick on ALL pages (broken on All-trips).
9. Reorder SceneDesc boxes: original→new changelog ABOVE the narration textarea.
Plus two mid-session asks: mirror the completed-trips Stage-9 handshake to R2; and
(critical) never overwrite corrected audio — masters are regenerable, confirmed takes
are expensive human work.

## What I did

### 3 — "people" cutoff (ROOT CAUSE, the important one)
`audio_splice.plan_whole` voiced whole/Q&A candidates with `generate_audio` (no
alignment), so their `meta` carried **no `cand_words`**. The `letter_end` trailing-trim
FLOOR added 2026-07-02 therefore *silently never applied* to whole/Q&A/`whole`-SceneDesc
regens — it only ever protected the segment-splice path. `trim_trailing_breath` was
cutting **187 ms into the final word at 1.0×, 314 ms at 0.7×** (reproduced live; the live
DB showed `cand_trim_ms=689.8` on exactly the Bath fid). Fixes:
- `plan_whole` now uses `generate_with_timestamps` → `cand_words` present → the floor
  fires. (Nothing downstream assumes whole meta lacks cand_words — verified.)
- `trim_trailing_breath(speed=…)`: its ms windows were tuned at 1.0×; a 0.7× CEFR take
  stretches articulation by 1/speed, so the time windows (NOT the dB bars) are divided by
  speed. `_CAND_TAIL_FLOOR_PAD_S` scaled the same way.
- Verified live: after the fix the trim keeps 130 ms (1.0×) / 186 ms (0.7×) past the word.

### 5 — pause-tool slowness
Every pause edit rewrote the working mp3 → invalidated the Whisper cache → the NEXT tool
call re-transcribed the whole clip (faster-whisper `small`, beam 5) on the laptop CPU.
Now `_reindex_word_cache` re-times the cached word timings arithmetically for a
PURE-SILENCE edit and re-stamps the audio-hash sidecar, so the next read is a cache hit.
`silence_edit=(a,b,ins)` on `_commit_working_edit`, passed only by the silence tools.
Also moved the R2 mirror off the request thread (single-worker pool).

### 2 + 6 — pauses on question audio + Insert 3s
Backend `insert_silence`/`remove_silence` were already field-generic; the FE simply never
rendered the buttons for `wholeOnly` (Q&A/option) fields. Wired them (surface = the
field's own textarea) and added **Insert 3s** alongside 1s/0.5s.

### 7 — Edit waveform (full editor, per dave's pick)
`WaveformEditor.tsx` + time-addressed backend ops (`waveform` envelope, `wave_insert_
silence`, `wave_delete`, `wave_silence`, `wave_move`). These deliberately have NO
text/aligner safety rails — they cut exactly where the human clicks — so they're also the
fast path. Same version-archive + coverage-reset backstop as every audio op. Delete/
silence/move correctly do NOT pass `silence_edit` (they can alter speech → honest
re-transcribe).

### 1 — 4K static-360 thumb
`static360.py`: index `D:\Final stitch\Re-Encodes\Static-Images-4k-mono` (4096×2048,
~1 MB vs 7680² ~15 MB), match trip→leaf, and **only trust a folder when its index set ⊆
the trip's static-scene indices** (a `Bude` folder holds 7,8,9,10 while `Bude_Beg` holds
6,7,8,9 — different numbering, would serve the WRONG panorama). Read model prefers
`{i}-4k.jpg`, falls back to the master. **Uploaded 452 objects to R2** (`review-audio`
→ actually the thumbs bucket `review-overlays/` prefix); live URLs verified 200. 24 trips
have no 4K re-encode (Takayama/Hida/Kaohsiung/Taiwan families) → they keep the master;
`--audit-4k` on the image uploader lists them by name.

### 8 + 9 — header + box order
All-trips page hand-rolled its own header instead of using `<NavBar>` → it scrolled away.
Now uses the sticky NavBar (with `backTo={null}`). SceneDesc diff moved ABOVE the
narration textarea via a new `diffAbove` prop on `EditableField` (SceneDesc only; short
fields keep it below).

### completed-trips handshake → R2 (mid-session ask)
Was a LOCAL file (`completed_trips.json`) → broke when the server moved to the laptop
(Stage 9 read a Windows copy frozen 2026-07-08). Now mirrored to
**bucket `review-audio`, key `_bus/completed_trips.json`** (`review_bus.put_completed_
snapshot`), best-effort (never fails an approve, loud on failure), fired on approve /
manual-complete / un-complete. Payload shape **unchanged**. `scripts/export_completed.py`
pushes too (default; `--no-push` opt-out; non-zero exit on mirror failure).
`COMPLETED_EXPORT_PATH` now env-overridable so the local write lands somewhere sane on
Linux.

### 4 — security audit (the real finding)
Permissions are clean: NO reviewer can write staging (every Firebase write → `commit()` →
`approve()`/`require_admin`; `submit()` writes nothing; structure/publish all
`require_admin`). R2 keys are built from server-side values; `import_mp3` discards the
client filename. BUT the audit found a **data-integrity bug**: on the masters-less laptop,
`promote` writes to an ephemeral seed-cache, so `review-audio/<cid>/<i>.mp3` is the ONLY
durable home of a corrected take — and both this app's combine-mirror AND the Scripts
bulk uploader overwrite it. Fixes (guided by dave's rule "corrected audio > originals"):
- `review_audio.preserve_original` archives the as-delivered master to
  `<cid>/originals/<i>.mp3` ONCE before the first overwrite (additive; never touches the
  canonical key). `download_dir` deliberately does NOT seed from originals/ (that would
  resurrect a stale master over corrections).
- `commit()` now mirrors the promoted master **synchronously** (`upload_master`) so a
  correction can't sit in a queue a restart drops; `review_audio.shutdown()` drains the
  pool on graceful stop (wired to FastAPI shutdown).
- `scripts/backfill_r2_originals.py` — restores the delivered masters R2 already lost
  (256 restorable from work/*/orig on the laptop; earliest-session-first).
- **Cross-repo (dynamic-content):** `Scripts/upload_review_audio_r2.py` now SKIPS any
  scene with an `originals/` marker (a confirmed take) unless `--force`; the 5c callers
  (`run_levels.py`, `gap_fill.py`) inherit the guard automatically. dave commits that repo
  via GitHub Desktop.

## Verified
- 3, R2-preserve, and the Scripts guard were tested against the LIVE ElevenLabs API / R2
  (not just unit logic): tail-trim keeps the word; corrected audio survives re-seed and
  the master is archived + recoverable; the uploader keeps corrected scenes and `--force`
  overrides.
- Red-team (`/red-fable`, cold Fable agent) found **3 real correctness bugs**, all fixed +
  independently re-verified by me:
  - BUG 1 (serious): `_reindex_word_cache` could stamp a STALE cache as current after a
    combine→trim_silence chain → next splice cuts mid-word silently. Fix: capture the
    pre-edit working hash in `_commit_working_edit` and re-time ONLY when the sidecar
    matches it (else leave stale → forces honest re-transcribe). Reproduced the failure +
    the guard + the still-working valid chain in a self-test.
  - BUG 2: end-anchored `trim_silence` left a final-word `end` overhanging the new clip →
    clamp to new duration. Verified.
  - BUG 3: `backfill_r2_originals.py` picked an arbitrary session's `orig/` → could
    archive a CORRECTED take as the "original"; now earliest-session-first + per-key.
  - Report-only (triaged, deferred): preserve TOCTOU pollutes only the ARCHIVE (never
    loses corrections); torn mirror reads self-heal via FIFO; static360 subset is a
    heuristic (honestly documented); whole-regen front-trim is benign.
- Gates green: backend `py_compile` OK; frontend `tsc --noEmit` exit 0; `npm run build`
  OK; `npm run lint` OK (in the red-team run).

## Deploy
Committed + pushed to `main`; laptop pulled + rebuilt + restarted (see the deploy note at
the end). **Post-deploy on the laptop:** run `backfill_r2_originals.py --apply` (restore
the 256 lost masters) and `export_completed.py` (seed the R2 completed snapshot from the
LAPTOP's live db — NOT the stale workstation copy).

## Open / TODO
- **dynamic-content commit (dave, GitHub Desktop):** `upload_review_audio_r2.py` overwrite
  guard. Until committed + on the workstation, a stage-5c re-run can still clobber a
  corrected take.
- 24 trips have no 4K panorama re-encode (Takayama/Hida/Kaohsiung/Taiwan families) — they
  serve the 15 MB master until re-encoded into `Static-Images-4k-mono`. `upload_review_
  images_r2.py --audit-4k` lists them.
- Deferred red-team nits (see Verified) — none blocking.

---

## Follow-up round (later same session, into 2026-07-15) — 3 more items

Deployed on `a15aac5` (live on the laptop).

### Nav "Task list" button
`NavBar` now renders an always-present "🗂 Task list" home button in the right cluster
(hidden only on the trip list itself via `backTo={null}`). The `←` back link stays but
can point elsewhere (structure editor → `/staging`), so this is the explicit one-click
home on every page.

### Q&A 3-second gap (SceneDesc-only tail)
`_target_tail_seconds(trip_id, field_path)` is now field-aware: the 3s beginner pause
(A1-2/N5/HSK1-2) applies ONLY to `SceneDesc`. Questions/options/titles keep the small
0.4s tail at every level. Confirmed the PIPELINE only pads narration
(`AddSilenceToLowLevelNarration`, `SCENE_RE = ^\d+\.mp3$`) — the Q&A gap came entirely
from the review app's `combine`/`trim_silence`. Existing Q&A takes self-correct on
re-combine or "Trim end silence" (now 0.4s for them). Wired at both call sites (combine +
trim_silence).

### Un-complete reopens the approved session (shadow-session bug)
**Symptom (dave):** un-completed Blaenavon_A12_EN → the trip list showed it as if never
reviewed. **Cause:** `approved` is the only terminal status, so opening an un-completed
trip re-seeded a BLANK session from the promoted masters (`sess_f62…`, all 22 versions =
seed-time v0), which shadowed the real approved `sess_3cb…` (newest-by-created_at wins in
`create_or_resume`). Same shadow shape statuses.py exists to kill. **Fix:**
`uncomplete_trip` reads the completed row's `session_id` before deleting it and flips that
approved session → `changes_requested` (EDITABLE), so the next open RESUMES it with edits
intact instead of re-seeding a blank. Manual completions (session_id NULL) just drop the
row.
- **One-time laptop cleanup (user-authorised, backup-first):** backed up review.db to R2
  (`_db-backups/review-20260714-234358.db` + `review-latest.db`), then a guarded txn (asserts
  both session identities + blank has zero non-seed versions before touching anything)
  deleted the blank `sess_f62…` + its work dir and flipped `sess_3cb…` →
  `changes_requested`. Blaenavon now has ONE session showing its real review.

### Note / possible follow-up
Other trips un-completed BEFORE this fix could carry the same blank-shadow. The code fix
prevents new ones; a sweep for existing blank-shadow sessions (a newer zero-edit in_review
session in front of an approved one) was NOT done — raise if wanted.
