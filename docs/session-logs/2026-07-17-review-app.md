# 2026-07-17 — review-app

## Session: EU reviewer accounts + German highlight-splice bug

**Goals:** (1) reviewer logins for ES/FR/DE/IT on the live laptop; (2) document
remote password management; (3) fix the German highlight-regenerate bug (highlight of
"Calw ist eine Stadt im Schwarzwald." re-voiced the next sentence too).

**What I did**
- **Bug root-caused & fixed (`9892cac`, live):** `sessions._whisper_orig` transcribed
  every working take with faster-whisper `lang="en"` hard-coded. EU trips now route
  Latin-script German/ES/FR/IT audio through the English splice engine, and forced-en
  Whisper *translated* the German take ("Kalf is a city in the Black Forest…" — pulled
  the actual cache off the laptop for `sess_08fd7c4e984a` field 2548). Almost no tokens
  aligned → `_silence_cut` skipped unmapped words (`continue`) → span silently expanded
  to the next mapped word, which was "Hirsau." at the end of the NEXT sentence. Fix:
  transcribe in `_whisper_lang(trip_id)` (via `audio_core.language_of`; en/es/fr/de/it,
  ja/zh honestly mapped though CJK uses the MMS aligner); `.audiohash` sidecar now
  stores `<hash>|<lang>` so the poisoned forced-en EU caches self-bust on next read
  (legacy hash-only sidecars read as lang=en — correct for every pre-fix cache);
  `_reindex_word_cache` parses/preserves the lang stamp and won't re-time a
  wrong-language cache.
- **EU languages in `manage.py`** (`777ee13`): VALID_LANGUAGES + comment tying it to
  `language_of` (they were unlistable, making EU trips admin-only).
- **Four reviewer users created on the laptop** (`spanish`/`french`/`german`/`italian`,
  each scoped to their language). Passwords generated once and handed to dave in-chat.
- **`docs/user-management.md`** — remote user/password management runbook (ssh form,
  the required `REVIEW_APP_SCRIPTS_ROOT` env prefix, reset-password semantics).
- **Process change:** `/red-opus`//`red-fable` are no longer auto-run before deploys —
  dave triggers them himself (memory updated).

**Verified**
- `language='de'` transcription of the failing take on the laptop: near-perfect words,
  clean 460 ms pause after "Schwarzwald." (2.38→2.84 s) — the same highlight now cuts there.
- Gates: `py_compile` + `import app.sessions` clean (twice — mine + red-team's).
- Red-team (clean-context Opus): no edits, no findings.
- Deploy: laptop pulled `9892cac`, `sudo -n systemctl restart review-app.service`,
  both `review-app` + `review-tunnel` active, uvicorn startup clean.
- Login as `german` through the tunnel: HTTP 200, role reviewer, languages ["German"].

**Open / low-urgency TODOs**
- The German reviewer's stale candidate on Hirsau_Abbey_A12_DE field 2548 (the
  over-long "Calw … Hirsau." take) is still attached — they should discard and
  re-run the highlight; the new plan will splice only the highlighted sentence.
- `_silence_cut`'s `continue` on unmapped words is still silent expansion by design;
  with correct-language transcription the map is dense, but a guard/log if >N
  consecutive words are unmapped would catch the next alignment pathology earlier.

**Next steps:** dave shares the four passwords with the reviewers; watch the first
real EU highlight/splice ops in the laptop journal.

---

## Checkpoint 2: pipeline report actioned (pad-marker fix)

**Goal:** review `Scripts\…\EU A12 Drafting\REVIEW_APP_REPORT_2026-07-17.md`, test its
assumptions, make the changes.

**What I did**
- **§1 pad-marker bug — confirmed and fixed (`5ea958e`, deployed).** Independently
  reproduced: the real padded master `Hirsau_Abbey_A12_DE/1.mp3` carries
  `comment=lowlevel_silence_pad` (tail measured 3.44 s); one pass through
  `mp3_to_samples → samples_to_mp3` and the tag is gone (WAV intermediate — no metadata
  can survive), so Stage 9's `pad_dirs` would pad again (+3 s on one scene). Fix at
  **promote time** in the approve path: `audio_io.tag_pad_marker` (new) stream-copies
  the working take with the marker (`-c:a copy` — decode verified bit-identical, passes
  the pipeline's exact `_is_padded` check), and the TAGGED file is what's promoted to
  the local master AND uploaded to R2 (R2 = the bytes the pipeline syncs back). Gated
  on `_target_tail_seconds == 3.0` (beginner SceneDesc only) AND measured trailing
  silence ≥ `_PAD_TAG_MIN_TAIL_S` (2.5 s) — an offline import that lost the pad stays
  untagged so Stage 9 supplies it. Best-effort (tag failure → promote untagged).
  CLAUDE.md Submit bullet documents it.
- **§2** `manage.py` VALID_LANGUAGES was already committed this morning (`777ee13`);
  set laptop `admin` to all seven languages (cosmetic — admins bypass scoping).
- **§3** verified: every `resolve_audio_dir` candidate is `_has_scene_mp3`-guarded. No change.
- **§4** no action owed. Replied to the pipeline session by appending to
  `Scripts\docs\session-logs\2026-07-17-dynamic-content-scripts.md` (their repo — dave
  commits it via GitHub Desktop).

**Verified:** tag round-trip test (untagged `_is_padded` False → tagged True, decoded
samples identical, duration 13.897 s unchanged); `py_compile` + `import app.sessions`
clean; laptop pulled `5ea958e`, service restarted, `review-app` + `review-tunnel` active.

**Open:** the pipeline session flagged a `Tokyo_01` TripGroup duplicate `tripId`
(`Tokyo_01_Beg_JP` twice; `Beginner+` presumably should be `Tokyo_01_Beg_N4_JP`) —
data fix awaiting dave, not code.
