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
