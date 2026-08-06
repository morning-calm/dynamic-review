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

---

## Later still — Option A built, tested and DEPLOYED (`462209b`, `dc31260`)

**Goal (dave):** "Build A" — wire the review app to the shared Scripts number-clean
prompts instead of its own English-only copy.

### What I did

**The wiring.** `config.py` puts `Scripts/Audio Generation/` on `sys.path`; `audio_core`
imports `gemini_number_clean_prompts.build_prompt` + `tts_number_clean` (DeepSeek
transport) + `korean_number_clean`, and its ~50-line hard-coded English Gemini prompt is
gone. `validate_and_clean` derives the language from `doc_id` (already the trip id — no
signature change) via `clean_lang_code` → `_LANG_CODES`. An unmapped language **disables**
cleaning and warns rather than defaulting to English.

- **ko** → `korean_number_clean.clean_field` (deterministic `sino_year` + acronym
  pre-expansion, its own Hangul-numeral guard).
- **zh/jp** → **not cleaned, by design.** `regenerate` voices their spoken line raw via
  `_cjk_spoken`, so cleaning only on the `fallback()` path would desync the reference clip
  from the working take. Also: the zh harness emits the year TWICE — `1999年` →
  `一九九九年（一九九九年）`, three identical runs — and its numeral-stripped guard can't see
  it. What they no longer do is fall through to the ENGLISH prompt.
- Pronunciation overrides reach the model for **en only**: non-EN prompts are
  target-language-only by Scripts design, `prompt_rule` is English prose, and fr/de/it/es
  have no `{extra}` slot anyway. `apply_overrides` (the load-bearing half) still runs for
  all — verified live, `Taipei101_HSK3_ZH` still substitutes `101`→`一〇一`.

**The guard had to change with it (unplanned, forced by measurement).** The first live run
came back with FR/EN/ES/DE all falling back. The model was right every time; the guard was
throwing the work away. Measured against the 0.80 bar on four *perfect* cleans: **en 0.62,
fr 0.71, es 0.40, de 0.47.** This is not new and not the DeepSeek move — the old
English-only path used the same word ratio, so **number-dense ENGLISH scenes have been
silently falling back to raw digits all along.** Scripts fixes this per language by
stripping numeral vocabulary from both sides, but only it/jp are registered. So
`clean_accepted` now defers to that inventory where it exists and otherwise uses two
vocabulary-free arms that must BOTH pass: **recall** of the non-convertible prose skeleton
(≥0.9 — number words the model added can't lower it) and a **growth** budget (≤8 words per
convertible token — recall alone scores an added paragraph 1.0). Comparison is
accent/case/punctuation-folded: one `recorrio`→`recorrió` repair had dropped a correct
Spanish clean to 0.667.

**Three defects found while building, all fixed:**
1. `_shared.needs_number_clean` counts "any Latin token of 2+ letters" as convertible —
   right for the CJK text it was written for, but on a Latin-script language every word
   matches and the gate never fires. Replaced with `_is_convertible`, the same predicate
   the skeleton is built from.
2. My first `_is_convertible` case-folded Roman numerals, so `de`→`D`, `Le`→`L`, `me`→`M`
   after stripping an ordinal suffix — the commonest words in the Romance corpus would
   have read as regnal numerals, hollowing out the skeleton AND handing the growth budget
   8 free words per article. Roman numerals are now matched only when UPPERCASE.
3. API failure now reports `used_fallback=True`. The old port returned the *input* on
   error, which scored 1.0 against itself and was reported as a **successful** clean — so
   the `edit_required` routing its own docstring promised could only ever fire on a
   similarity miss, never on an outage.

**Cache-key bump.** `sessions._cleaned_orig` mixes `audio_core.CLEANER_VERSION` into its
hash. A cached entry asserts what the working audio *says*; sessions seeded before today
hold English-numbered baselines for FR/ES/DE/IT/KO trips and must re-clean on resume
rather than feed the splice engine phantom diffs around every number.

**Startup visibility.** `main._startup` logs `audio_core.cleaner_status()`. A missing
`Audio Generation/` degrades to "no cleaning + edit_required", loudly — this is the
dependency class that failed silently for jieba (07-08) and opencc (07-29).

### ⚠ The one that nearly went to production
The pre-restart smoke test on the laptop caught it: that checkout was **30 commits behind**
and its `tts_number_clean` has `build_prompt` and all nine prompts but **no
`similarity_basis` / `clean_similarity`**. `clean_accepted` reached for them unguarded, so
every regenerate on the live host would have raised `AttributeError`. Fixed in `dc31260` —
the inventory is feature-detected and is an upgrade, never a dependency. *Run the app's own
code against the laptop's checkout before restarting, not after.*

### Verified
- Backend suite **111 green** on the workstation (31 new in
  `tests/test_number_clean_language.py`, incl. adversarial guard cases and a test that
  simulates the older Scripts checkout).
- **Live DeepSeek, all 8 languages, on the workstation AND again on the laptop's own
  checkout** — fr/en/es/it/de/ko all correct in-language; fr with no numbers makes no API
  call; zh/jp untouched.
- Laptop: `review-app` @ `dc31260`, `Scripts` pulled (`6988ee0`→`b88cd55`), service
  restarted, `systemctl is-active` = active, `api/health` 200, **uvicorn AND cloudflared
  both up**, tunnel re-registered (lhr13/lhr21). Startup line reads
  `[startup] number-clean OK: deepseek-v4-flash (de, en, es, fr, it, jp, ko, zh)`.

### Open / carried forward
- ⚠ **dave: push the workstation's `dynamic-content` commits.** `c3b87eea` ("Number
  cleaning: translator-reported TTS fixes across JP/KO/EU") is committed locally but NOT on
  origin, so the laptop is running the pre-translator-pack prompts. The app works on either;
  pushing gets the reviewers the refined ones.
- **zh harness emits the year twice** (`一九九九年（一九九九年）`) — Scripts-side, reproducible,
  invisible to its own guard. Blocks turning zh cleaning on.
- **Register fr/de/es in `tts_number_clean._STRIPPERS`** — would let `clean_accepted` defer
  to Scripts everywhere and retire our recall/growth arm; also fixes the same rejected-clean
  bug in the pipeline's own templates.
- **Non-EN prompt templates have no `{extra}` slot** (only en/zh/jp/ko do), so per-trip
  pronunciation-override *reinforcement* is dropped for fr/de/es/it. Degradation, not
  breakage — `apply_overrides` still substitutes the text.
- `scripts/backup_review_db.py` **aborts on the laptop**: "R2 creds missing" although
  `.env` holds 5 `Cloudfare_*` keys — an env-loading path issue, not absent creds. review.db
  is the only copy of review state; worth a look.

---

## 00:15 — dave pushed `dynamic-content`; thorough verification on the live host

**Goal (dave):** "I pushed it. Test it thoroughly. No chrome playthroughs."

Laptop `Scripts` pulled `b88cd55` → `9908685`; **`c3b87eea` (the translator-pack prompts)
is now present**. Verified the app against the new revision BEFORE restarting (the same
gate that caught the AttributeError last round): all modules import, `similarity_basis` /
`clean_similarity` / `needs_number_clean` now exist, inventory registered for it+jp.
Restarted at 23:57; startup line OK, health 200, uvicorn + cloudflared both up.
Workstation and laptop are now on the SAME Scripts commit, so the local suite tests the
production revision.

### 1. Whole real corpus, language-leak check — **316 scenes, 0 leaks, 0 errors**
Ran every numbered `SceneDesc` in the live review.db through `validate_and_clean` and
scanned each output for English number/ordinal/unit vocabulary.

| lang | n | cleaned | fallback |
|---|---|---|---|
| en | 188 | 188 | 0 |
| fr | 70 | 70 | 0 |
| jp | 26 | 26 (untouched by design) | 0 |
| de | 15 | 15 | 0 |
| zh | 11 | 11 (untouched by design) | 0 |
| es | 6 | 6 | 0 |

**100% acceptance in every language, no exceptions raised.** The 5 flagged "leaks" were all
my detector's fault — French `cinquante-six`/`vingt-six` split on the hyphen and `six` is
also an English word. Manually checked all five: correct French.

### 2. Old guard vs new, same cleaned output — the guard change earns its place
| lang | n | OLD accept | NEW accept |
|---|---|---|---|
| es | 6 | 67% | **100%** |
| fr | 30 | 93% | **100%** |
| en | 30 | 100% | 100% |
| de | 15 | 100% | 100% |

**Every disagreement is old-REJECTS / new-accepts, and every one of those cleans is
correct.** There is no case of old-accepts / new-rejects — the new guard rescues correct
work without loosening anything the old one caught. Examples:
`«¡Tienen 351 escalones!» → «trescientos cincuenta y un escalones»` (word ratio 0.737),
`«a commencé en 1789 et a fini en 1799»` (0.720). Short number-dense A12 sentences are
exactly where the word ratio fails, and A12 is most of the EU queue.

### 3. The stale cached baselines — the bug caught in production data
Read-only scan of `sessions.cleaned_orig_json`: **17 of 98 cached baselines contain English
number words on a non-English trip.** These are the splice engine's diff baselines, i.e.
its record of what the audio says:
- `Reims3_A12_FR` — *"La Révolution française a commencé en **seventeen eighty nine** et a
  fini en **seventeen ninety nine**"*
- `Reims3_A12_FR` — *"Cette sculpture est de **fifteen thirty one**"*
- `Baden-Baden_A12_DE` — *"Der Turm ist aus dem Jahr **nineteen sixty one**"*

Confirmed the `CLEANER_VERSION` key change makes every one of them MISS, so they re-clean
in the trip language on next use. `Reims3_A12_FR` scene "1789/1799" is the whole arc in one
scene: cleaned into English → cached as English → now cleans into French AND the cache is
invalidated.

### 4. The two things the reviewer actually named
Dates and numbers after monarchs, across all four EU languages — all correct:
`Louis XIV → Louis quatorze`, `Napoleon III → Napoleon trois`, `Louis XVI → Louis seize`,
`XIIe siecle → douzième siècle`, `1215 → mille deux cent quinze`;
`Alfonso X → Alfonso décimo` (RAE ordinal-to-10 rule); `Friedrich II → Friedrich der
Zweite`, `1745 → siebzehnhundertfünfundvierzig`; `Carlo V → Carlo quinto`,
`1536 → millecinquecentotrentasei` (single word, per the translator fix).

### 5. Korean on real staging content (no KO sessions exist yet, so read from Firebase)
5 scenes across `Busan_Oryukdo_TPK1/TPK2_KO`, all correct — including the **irregular
month forms** the translator pack added: `6월 → 유월` (not 육월), `10월 → 시월` (not 십월).
`1950년 → 천구백오십 년`, `2013년 → 이천십삼 년`, `35미터 → 삼십오 미터`.

### 6. Everything else
- **Determinism**: 3 identical runs, FR and EN, byte-identical output (temperature 0).
  Matters because `_cleaned_orig` caches one clean and the splice engine compares it with a
  second independent clean — drift becomes a phantom diff op.
- **Edge cases**: empty / whitespace / None / bare number / no-numbers / punctuation-only /
  single char / URL / mixed scripts / unmapped trip suffix — all handled, none raised.
  A 40×-repeated sentence falls back (the model deduplicates it); the **longest real** EU
  scenes (up to 1635 chars) all clean at recall 1.000, so this is not a length problem.
- **Pronunciation overrides**: survive the clean on EN (`Oryukdo → oh-ryook-do` kept while
  `1950 → nineteen fifty` expanded), and the `prompt_rule` reinforcement block is present in
  the EN prompt. On non-EN the substitution still applies (verified `ang-pau → 昂包`).
- **Live service**: health 200, `/api/trips` without a token 401, **zero tracebacks /
  AttributeErrors / 500s** in the journal since restart, and an authenticated client kept
  polling the nav endpoints (`bug-reports/count`, `presence`, `recall-requests/count`)
  throughout without error. NB that is a tab left open, not someone reviewing — `french`'s
  presence heartbeat stopped at 23:26:17 and the restart was 23:57, so no reviewer was
  interrupted.
- **Suite**: 111 green against the production Scripts revision; both inventory branches
  (`test_registered_inventory_defers_to_scripts`, `..._without_the_inventory`) run and pass.

### Still open (unchanged)
zh year duplication (`一九九九年（一九九九年）`), fr/de/es strippers not registered upstream,
no `{extra}` slot in non-EN templates, `backup_review_db.py` aborting on the laptop.
All in BACKLOG 0n/0o.

---

## 00:40 — Fable red-team, fixes, redeploy (`e91ab38`) — SESSION CLOSE

**Goal (dave):** "red fable code changes, tell me what time the french reviewer stopped
working, then commit and close out session."

### French reviewer's stop time
**23:26 on Thu 6 Aug.** Last text edit 23:26:06 (`Reims3_A12_FR`), session submitted
23:26:13, presence heartbeat 23:26:17 ("viewing changes"). Their day: `Monaco2_Beg_FR` 13:33
→ `Monaco2_A12_FR` 13:34 → `Monaco2_FR` 15:02 → `Hyeres_A12_FR` 20:05 → `Reims3_A12_FR`
23:26. Nothing in review.db touched since. **Both of tonight's restarts (23:57, 00:38) were
after they stopped — nobody was interrupted.** Note the last trip they submitted,
`Reims3_A12_FR`, is the one whose cached baseline read *"commencé en seventeen eighty nine"*.

### Red-team (fresh clean-context Fable agent) — 3 real bugs, all in code written today
All three verified by me against the pre-fix logic before accepting the fix; gates re-run
myself (**120 passed**, ruff clean on the changed files); its diff read in full and confirmed
in scope.

1. **An EMPTY model response was accepted as a successful clean** when the input was
   all-convertible (a year-only quiz option, `"1868."`). Empty skeleton ⇒ recall vacuously
   1.0, empty output inside the growth budget; the Scripts it/jp arm has the same hole (both
   sides strip to `""` → 1.0). Reproduced against the pre-fix code verbatim:
   `clean_accepted('en','1868.','')` → **True**, likewise it/fr/`5 km`. DeepSeek returning
   empty content is documented upstream (token budget spent on reasoning). Now rejected;
   confirmed it rejects nothing it should accept.
2. **Same hole in the Korean branch**, which never reaches `clean_accepted` —
   `_LEFTOVER_NUMERIC_RE` finds nothing numeric in `""`, so empty was returned as
   `("", False)`, i.e. a success, against the function's own contract.
3. **`SystemExit` escaped `except Exception`.** `korean_number_clean._deepseek` raises
   `SystemExit` on a missing key (I confirmed the literal line in the Scripts module) — a
   BaseException. On a host in exactly the degraded state `_startup` warns about, every
   Korean clean would have **crashed the request** instead of falling back. Same
   laptop-env-gap class as jieba/opencc.

Plus its hardening: `clean_accepted` now feature-detects **`clean_similarity`** (the attribute
it *calls*), not only `similarity_basis` — `dc31260` guarded the sibling and then called the
other unguarded, which could have re-created the very AttributeError it was written to prevent.

**It also caught a vacuous test of mine**: `test_cleaner_version_is_in_the_cache_key` asserted
the constant appeared in `inspect.getsource(_cleaned_orig)` — and the *docstring* names it, so
it stayed green with the code reverted. Now pinned on an extracted `sessions._cleaned_cache_key`
with a monkeypatched version. I verified the extraction hashes **identically** to what was
already deployed (`e8452f483ca1`), so there is no second re-clean wave.

### Two fixes of my own, from triaging what it reported but left alone
- **`Ier`** (François/Albert Ier) was not convertible — it survives neither the uppercase-Roman
  test nor the ordinal-suffix strip. **Four occurrences in the live corpus, all on the Monaco
  trips the French reviewer is working**, all passing but at recall 0.944–0.987 (one skeleton
  word down), so a shorter sentence carrying it would have lost a correct clean. Fixed as
  whole-token equality, never a suffix rule — extending the strip set to `r` makes `LIVRE`
  convertible, and any "roman core + lowercase suffix" rule reads `Le`→`L`, `de`→`D`.
- **`cleaner_status()` listed zh/jp among the cleaned languages**, so the startup line told an
  operator they were being cleaned when they are deliberately passed through. Split into
  `languages` / `not_cleaned`; the line now reads
  `cleaning de, en, es, fr, it, ko; passthrough jp, zh`.

### Triaged, deliberately NOT changed (in BACKLOG 0p)
Retry amplification (3 × 4 attempts on a hard DeepSeek outage), the dead `scene_index`
param, `_is_convertible`'s remaining marginals (standalone `I`/`M.`, all-caps acronyms —
all conservative-direction), and the twin regexes `_LEFTOVER_NUMERIC_RE` /
`_CONVERTIBLE_TOKEN_RE` (identical character class, different roles — do not let anyone
"deduplicate" one without reading both comments).

### Verified + deployed
120 tests green; ruff clean on changed files; laptop pulled to `e91ab38`, imports checked
BEFORE restart (the gate that caught the AttributeError earlier), restarted 00:38,
`is-active` active, health 200, uvicorn + cloudflared + tunnel all up, new startup line
correct, zero tracebacks.

**Session closed.** The reported bug is fixed, verified on 316 real scenes, and live.

---

## 00:55 — Follow-up: is the zh/jp passthrough also a gap in the Scripts repo?

**Question (dave):** why aren't ZH and JP cleaned, and is that a gap on the Scripts side too?

**Answer: no — Scripts is fine. The gap is entirely app-side, and it is older and wider than
number cleaning.** My earlier note called the passthrough "by design"; that was under-stated
and I have corrected it in `audio_core._NO_CLEAN_LANGS` and `CLAUDE.md`.

**The pipeline cleans all three CJK languages before TTS** (verified in `dynamic-content`):
- **zh** — the three `multiple_documents_*_ZH.py` voice templates `from mandarin_number_clean
  import clean_field`.
- **jp** — the six JP templates carry their own `clean_text`/`validate_and_clean` over
  `build_prompt("jp", …)` + `needs_number_clean` + `clean_similarity`, and the call order is
  `process_text()` (take the last/kana line) **then** `validate_and_clean` on it — i.e. the
  pipeline cleans *exactly the string this app voices*.
- **ko** — `multiple_documents_Korean_KO.py` → `korean_number_clean.clean_field`. The app
  matches this one (`ko` is not in `_NO_CLEAN_LANGS`).

**So a CJK master was voiced from CLEANED text and an app regenerate voices RAW text.**
`sessions.regenerate` sends zh/jp through `_cjk_spoken` → `plan_whole(cjk_new)` and never
calls `validate_and_clean` at all — so this is NOT something the 2026-08-06 work introduced;
the only CJK path that ever reached the cleaner was `fallback()`, and until yesterday it was
being handed the **English** prompt. That part did get strictly better.

**Second, larger half of the same bypass: pronunciation overrides.** `apply_overrides` is
applied in exactly ONE place in the whole backend — `audio_core.validate_and_clean:548`. So a
`_ZH` regenerate also drops the trip's pinned spoken forms. `Taipei101_HSK3_ZH` pins
`台北101 → 台北一〇一` and is hit by both halves at once.

**Measured exposure** (audio-bearing fields whose SPOKEN line carries digits — kana line for
jp, Hans for zh):

| | audio fields | exposed | trips |
|---|---|---|---|
| jp | 208 | **32** | Tokyo_03_Beg_N4, Tokyo_04_Beg_N4, KochiCity_N3, Yusuhara_Beg_N4 |
| zh | 330 | **19** | Taipei101_HSK3, Taipei101_HSK12 (all `台北101`) |

Real examples: 「せかいでも3ばんめにたかくて、たかさは634めーとるも…」, 「100めーとるぐらいです。」

**Why I did not fix it tonight.** It is not a flag flip. `cjk_splice` char-diffs OLD→NEW and
reads cut times from the forced aligner against the real audio, so OLD (`working_hans` / the
stored kana) and NEW would both have to live in cleaned space, with `working_text` /
`working_hans` re-baselined there — otherwise every surgical CJK splice mis-locates its cuts.
It also needs the zh year-duplication fixed upstream first, or zh would voice the date twice.
Filed as **BACKLOG 0q** with the measurement and a suggested order of work.

Docs corrected, tests still 120 green, ruff clean. No behaviour change in this follow-up.
