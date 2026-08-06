# 2026-08-06 — review-app

## 00:00 — Korean review goes live: reviewer account + the restart nobody had done

**Goal (dave):** a Korean reviewer who can see the Korean trips, with a password created for
them; "K Trips should be live".

### What was already true (measured, not assumed)

The 2026-08-05 late session (both repos) had done all the pipeline and app-code work:
- **Manifest**: 357 rows including all **24 `_KO` rungs** at lane 6, each with a measured
  duration and a non-blank family voice (Anna Kim ♀ / Hyuk ♂, 4/4 split). Committed as
  `6e42aeb` and already on the laptop.
- **App code**: `_KO → Korean` in `audio_core.language_of` + Whisper `ko` + both narrators
  (`0408790`), Korean scene images off the English parent (`4be3a3f`), `manage.py`
  `VALID_LANGUAGES` gaining Korean (`e121f1e`).
- The laptop's pull cron had **fast-forwarded to `e121f1e` at 23:50**, and both
  `review-app` + `review-tunnel` were active.

So on paper this was a one-command job. It was not.

### The blocker: pulled ≠ running

`systemctl show review-app -p ActiveEnterTimestamp` said the process had been up since
**2026-08-05 12:52** — but the Korean commits landed 23:13–23:46 and were pulled at 23:50.
**The live process was still serving the pre-Korean `language_of`**, which fail-safes an
unrecognised suffix to **English**. Creating the reviewer against that process would have
produced a silently broken account: 403 on all 24 rungs, an empty trip list, and code on
disk that reads perfectly correct. The auto-pull cron pulls; it never restarts.

Recorded as the third occurrence of the laptop-deploy failure class (memory
`laptop-env-pinyin-incident`): **`git log` on the laptop tells you what was pulled, not what
is running** — compare the ref mtime / `git reflog` against `ActiveEnterTimestamp` before
calling any backend change live.

### Done

- **Fresh WAL-safe backup first.** The newest was `review-20260805-020001.db`, ~22 h stale
  (it predated the whole 08-05 working day) because the 03:00 cron had not yet run — it was
  00:04. Took one by hand → `review-20260805-230428.db` + `review-latest.db`, 5,451,776 B.
- **Created the reviewer** on the live DB:
  `manage.py add-user --username korean --role reviewer --languages Korean`
  → `created reviewer 'korean' languages=['Korean']`. Password printed once and handed to
  dave in-session (PBKDF2 hash only is stored; not recoverable, not written here).
  - **Name is a placeholder, dave's call**: the EU convention (`french`/`german`/…) rather
    than a personal one, because the actual reviewer is not named anywhere. The Scripts log's
    `<sunyoung>` was a template placeholder, never an identity.
  - **Email deliberately unset**, dave's call → BACKLOG **0i** (with the toshifumi watermark
    warning: set it *before* the first Korean Gate-2 ingest or those findings are badge-only
    forever).
- **Restarted the backend** with the NOPASSWD form
  `sudo -n /usr/bin/systemctl restart review-app.service` at **00:05:15**, in a genuinely
  idle window (00:05).

### Verified

- **ACL is exact**, in-process on the live host with the service's own env: user `korean` /
  reviewer / active / `['Korean']`; of 357 manifest rows **24 visible**, mismatch sets in
  **both** directions empty (no non-KO leaking in, no KO rung withheld). `language_of` over
  all 24 → `{'Korean'}`; `_whisper_lang` → `ko`.
- **Audio genuinely resolves** (this is what makes a trip openable, not just listed):
  `resolve_audio_dir` pulled `Busan_Songdo1_Beach_TPK1_KO` (7 bare `<i>.mp3`) and
  `Gyeongju1_Bulguksa_TPK2_KO` (17) from the R2 `review-audio/` mirror into the seed cache —
  no local masters needed on the laptop.
- **Publicly live**: `GET /api/health` 200, `GET /` 200, `GET /api/trips` **401** unauthed
  (auth enforced). Journal clean since the restart, and a reviewer at 187.40.250.12 was
  polling `/api/presence` throughout — the restart did not disturb an in-flight session.
- `admin`'s ACL list has no Korean, which is **harmless**: `auth.language_allowed` returns
  True for role `admin` before consulting the list. Confirms the 08-05 log's "admin-only"
  claim was accurate.

Nothing was committed in this session — the work was operational (live DB + service), not
code. Docs/memory changes are in this repo only.

### Open / low-urgency TODOs

- **BACKLOG 0i** — set `korean`'s email; decide whether the account becomes a named person.
- Carried from 08-05 Scripts, unchanged and NOT addressed here (all pipeline-side):
  - 4 static-360 scenes show "no thumbnail" (Gyeongju1_Bulguksa 7, Gamcheon1 13,
    UNMemorial 11 + 14) — panorama placement blocked on the lane-9 ogg step.
  - 0 Korea TripGroups / TripLocations → no level buttons yet; app tolerates it.
  - KO scene indices run +1 against the English rung (shared GE placeholder).
  - The 13 Korean-name pronunciation respellings in the ENGLISH narration have still never
    been heard by a native speaker.
  - Scripts repo was committed to main but **not pushed** as of the 08-05 log — worth
    confirming.
- BACKLOG 3c/3d, 0h (john's email), `docs/auto-review-redesign.md` items 5–6, and the
  `french` reviewer's 4 open Monaco findings all still open from 08-05.

### Next steps

1. Give the `korean` account's credentials to whoever is reviewing, and set their email.
2. Watch the first Korean session for splice behaviour — Korean has never run through the
   splice engine live; `_whisper_lang` is right, but no Korean audio has been spliced yet.
3. English VR check (lane 4b) and the GE renders remain dave's calls on the Scripts side.

---

## Later — French reviewer: "numbers go back to English on regenerate" (investigation, no code changed)

**Goal:** French reviewer reports that fixing pronunciation / regenerating a whole sentence
sometimes makes numbers (dates, regnal numerals) come out in ENGLISH. Question asked: does the
review app use the Scripts-repo number-clean prompts (recently moved Gemini → DeepSeek, with
per-language prompts), and has the laptop pulled?

### Findings (measured)

1. **The review app has never used the Scripts prompts.** `backend/app/audio_core.py:267-326`
   holds its OWN hard-coded, **English-only** prompt, still on **`gemini-2.5-flash`** — a 2026
   port of `RegenerateSceneAudio-EditMe.py`. Nothing in `backend/` imports
   `tts_number_clean` or `gemini_number_clean_prompts` (grepped). So the DeepSeek switch and the
   new per-language prompts never reached the app, and no amount of pulling would have changed
   that.
2. **Laptop git state:** `review-app` is current (`abc1118` = workstation HEAD, clean).
   **`Scripts` is 30 commits behind** — HEAD `6988ee0`, last fetch 2026-08-05 12:53 — missing
   `c3b87eea` "Number cleaning: translator-reported TTS fixes across JP/KO/EU". Secondary: the
   app doesn't call those modules anyway.
3. **Why it's the ENGLISH prompt for a French trip:** `validate_and_clean` is called with no
   language argument at all (`sessions.py:1464, 2382, 2396, 2662`). `audio_core.language_of()`
   exists and is correct, it is simply never consulted here. The prompt's own rules — "1868 →
   eighteen sixty eight", "Louis XIV → Louis the Fourteenth", "km becomes kilometres" — are then
   applied verbatim to French text.
4. **Why "sometimes" (the guard, measured):** `validate_and_clean` accepts a clean at word-ratio
   ≥ 0.80. A long French sentence with ONE year scores 0.93 → **accepted → English numbers get
   voiced**. A short or number-dense one scores 0.59–0.72 → rejected → falls back to raw digits,
   which ElevenLabs v2 usually reads correctly in French. Same trip, different sentences,
   different outcome — exactly the reported intermittency.
5. **Blast radius: 167 of 357 queued trips** (ES 83, FR 31, **KO 24**, DE 15, IT 14). ZH/JP are
   safe — `sessions._cjk_spoken` routes them past the cleaner entirely (the comment at
   `sessions.py:2321` literally says "the number-speller is English-only"). **Korean is NOT in
   that branch** (`_cjk_spoken` handles Mandarin + Japanese only), so the 24 `_KO` rungs that
   went live yesterday are exposed.
6. **Second, unreported symptom:** `_cleaned_orig` (`sessions.py:1453`) is the splice engine's
   diff baseline. On an EU trip it produces English number words while Whisper transcribes the
   master's actual French ones — so the anchors disagree around every number. This degrades
   highlight/alt splices on EU trips, not just whole-regen.
7. **The shared module works and is reachable.** Smoke-tested on the laptop venv against the
   OLD checked-out revision: `clean_once("fr", "…sous Louis XIV en 1668…")` →
   *"sous Louis quatorze en mille six cent soixante-huit"*. `DeepSeek_API_KEY` is present in the
   laptop `.env`; `deepseek-v4-flash` resolves. `tts_number_clean` self-loads that `.env`.
8. **Two gaps found in the Scripts side while there:** the non-EN prompt templates have no
   `{extra}` placeholder (only `en` does), so per-trip pronunciation-override *reinforcement* is
   silently dropped for fr/de/es/it — the `apply_overrides` text substitution still happens, so
   this is a degradation, not a break. And `Scripts/Audio Generation` is not on the review app's
   `sys.path` (`config.py:28-30` adds only SCRIPTS_ROOT + RW_STAGES).

### Verified
- Grep: zero references to the shared cleaner anywhere in `backend/`.
- `git log 6988ee0..HEAD` on the workstation Scripts repo = 30 commits.
- Similarity-ratio arithmetic reproduced locally on three representative French sentences.
- Live DeepSeek call from the laptop venv returned correct French.
- Laptop is up (uvicorn + cloudflared both running).

### Migration gotcha for whichever fix lands
`_cleaned_orig` caches into `sessions.cleaned_orig_json` keyed **only on a hash of the raw text**
— no cleaner-version component. Changing the cleaner leaves existing EU sessions resuming against
an English-numbered cached baseline → phantom diffs. The fix must bump that cache key (add a
`CLEANER_VERSION` to the hash) or clear the column for affected sessions.

### Next steps
Proposal delivered to dave (4 options: wire to the shared module / re-port the prompts /
language-lock the existing Gemini prompt / skip cleaning for non-EN). Recommendation: skip-for-
non-EN as a same-day hotfix, then wire to the shared module properly. Awaiting his call — **no
code changed this session.**
