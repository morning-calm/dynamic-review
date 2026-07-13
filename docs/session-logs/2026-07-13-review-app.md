# 2026-07-13 — review-app

## Session 1 — Ted's six scripts: audit the AI's comments, then replace its worst job

### Goals
1. Collate every comment on the six Mandarin scripts Ted edited into one document.
2. Answer: does "word above HSK level" come from a systematic check or from the LLM?
3. (Then, on dave's call) ship a deterministic level checker, and make the LLM's findings go
   to the REVIEWER for a response instead of dying in the admin's inbox.

### What I found (the audit)
The six scripts = Taichung / Taipei101 / KaohsiungLotusPond, each HSK12 + HSK3.
Three independent comment streams, and dave was right to be suspicious of one of them:
- **Gate 1** (`auto_checks.py`, deterministic) — script consistency only. **No level check at all.**
- **Gate 2** (`scripts/claude_review.py`) — **Claude Sonnet**, not Gemini. Every "above HSK3"
  comment was its *opinion*. (Gemini 2.5 Flash is in the codebase but only as the TTS number
  speller — it never sees review edits.)
- **Ted himself** — field comments + **8 open bug reports, all unanswered since 07-09**, all the
  same TTS defect: ElevenLabs voices 2nd-tone syllables (楼/球/人/城市) as 3rd tone. This is
  blocking him on Taipei101_HSK12 and is the biggest single item in the whole batch.

**Then I ran the pipeline's real checker** (`level_check.py`, jieba + `hsk_vocab.xlsx`) over all
59 changed fields, diffing out-of-band words orig→cur. Sonnet made **7 level judgments: 3 right,
4 wrong**, and missed 5 out-of-band words entirely (4 of which it had explicitly *cleared*):
- Wrong: flagged 离开 (**HSK2**) and 保持 (**HSK3**, it said "≈HSK5") as above-level; invented band
  numbers; **cleared 旧 as "HSK2" when it's HSK3** — out of band for an HSK12 trip.
- Right: 巨(龙), 铁轨, 损坏, 移动, 平衡.
- The 老旧 question (10 of Taichung_HSK3's 11 warnings): **in band** — by the pipeline's own
  *compound-rescue* rule (unlisted word, all chars in-band). Not "an HSK3 word", but in band by
  the same rule the drafts were written to. Rejecting that is a pipeline-wide policy call.
- Bonus: **Taipei101_HSK12 scene 7 has 堅固 — traditional chars in the Simplified box.** That's a
  Gate-1 block and will bounce Ted's submit. Tell him.

Doc: `docs/reviews/2026-07-13-ted-six-scripts-collated.md` (all comments, collated + scorecard).

### What I built
**(A) Gate-1 deterministic level check** — `backend/app/zh_level.py`, wired into `auto_checks`
as **WARN, never block** (out-of-band = a legitimate i+1 choice, just a conscious one). Reports
only what the *edit introduced* (diff orig→cur), so nobody is nagged about the draft's own i+1.
**LEVEL deleted from the Sonnet prompt.**
- ⚠️ **Cross-host trap found:** `*.xlsx` is **gitignored** in dynamic-content, so `hsk_vocab.xlsx`
  exists ONLY on the workstation — the live laptop has the Scripts checkout but not the data.
  Importing `level_check` would have worked here and silently failed in production (the same
  laptop-env class as the 07-08 pinyin incident). So: `scripts/export_hsk_vocab.py` snapshots the
  reference to the committed `backend/app/data/hsk_vocab.json`, and zh_level reads that + jieba
  (present in the live venv). Re-run the export when the HSK reference/proper nouns change.
- Verified: reproduces the `level_check` run on Ted's edits EXACTLY (same words, same levels).

**(B) Gate-2 findings go to the REVIEWER** (`auto_review_ingest.py`, new status `ai_review`).
submit → cron → any warning/needs_human → findings created, session CAS'd `submitted`→`ai_review`
(back with the reviewer, editable; approve blocked since it only claims from `submitted`). The
reviewer answers each: **resolved** / **rejected** (note REQUIRED — the admin reads it instead of
the change) / **deferred** (it's about the English → admin's call). Re-submit 409s `findings_open`
until all answered. Admin sees every answer+note on the Changes page and has a **"take it back
now"** override (`/findings/skip`) so the gate can never wedge a trip. Reviewer told by nav badge
+ email (`activity_notifier.notify_reviewer_findings`; needs `users.email` — `manage.py set-email`).

### Verified
- zh_level vs `level_check.py`: identical output on all 59 of Ted's changed fields; degrades to
  silence (None) on non-HSK trips / missing jieba / missing snapshot.
- Full triage workflow driven end-to-end against a **copy of the live review.db**: ingest → bounce
  to ai_review → submit blocked → reject-without-note refused → all three answers → submit
  unblocks → admin skip override → badge scoping (ted sees his; the Japanese reviewer sees 0) →
  a clean report correctly creates nothing.
- Notifier dry-run: no history blast on first run; silent skip + hint when no email set; one
  email per session.
- Gates: backend imports; `npm run build` clean; `npm run lint` clean.

### Red-team (/red-fable — dave's standing pre-deploy rule). Found 3 REAL bugs, all fixed:
1. **Livelock + destroyed evidence (the serious one).** `ingest()` DELETEd all prior findings on
   re-review. Reject-with-note → re-submit → cron re-reviews the *same* diff → same flags →
   the rejection note the admin is owed was deleted before he ever saw it, and the trip bounced
   back to `ai_review` forever. Fix: `rejected`/`deferred` answers are **carried forward** onto a
   re-reported identical finding; the CAS only fires if something is genuinely open. (`resolved`
   deliberately re-opens — that text changed, so the re-flag is a fresh judgment.) I re-traced
   this myself over 5 rounds: notes survive, all-rejected goes to the admin, a genuinely new
   finding still bounces.
2. **Triage wedge outside ai_review**: report lands during `approving` → CAS no-ops → approve
   fails → reverts to `submitted` → reviewer recalls → `in_review` **with open findings**: submit
   409'd, panel was read-only, skip 409'd. Stuck without raw SQL. Fix: panel `readOnly` now keys
   off `isEditableStatus` (matching the backend, which already allowed it).
3. **Notifier partial-failure**: `send_via_mailwizz` only catches `HTTPError`; a `URLError`/timeout
   escaped before the watermark advanced → whole batch re-emailed. Fix: any send raise = failed
   send for that session; the stop-short watermark then handles it.

It also flagged a **proper-noun divergence** I then fixed myself: `level_check` calls
`jieba.add_word()` on multi-char names so 太阳饼 isn't split into 太阳+饼 (leaking 饼 as
out-of-band). We must NOT mutate jieba's process-global tokenizer in a long-lived server
(hsk_lib's pinyin regen shares it). Implemented **proper-noun span masking** instead — same
answer, no global state. Verified: 太阳饼/宫原眼科/彩虹眷村 now exempt, 铁轨 still flagged, and
Ted's 59-field results are unchanged.

### Deployed (2026-07-13, commit 2167ba6 — LIVE on the laptop)
DB backed up to R2 first (`review-20260713-101925.db`), then: pull → `npm run build` (bundle hash
`index-DI8LFKa_.js`, identical to the local build) → `systemctl restart review-app.service`.
Verified ON THE LAPTOP (the cross-host risk was the whole point):
- `zh_level.checker_for('Taichung_HSK12_ZH')` → **OK**, 3397 surfaces; 旧→out(HSK3), 离开→in(HSK2),
  铁轨→out; proper-noun mask works (太阳饼 → no flag); non-HSK trip → None. The committed snapshot +
  the venv's jieba resolve correctly with no xlsx present.
- Migrations ran on restart: `users.email` present, `auto_review_findings` created (0 rows).
- `scripts/claude_review.py` and `activity_notifier.py --dry-run` both run clean on the new code
  (the shared-ingest import resolves; the findings watermark seeded without blasting history).
- Public: `https://review.dynamiclanguages.org` HTTP 200 serving the new bundle; the new
  `/api/findings/inbox` is auth-gated (401 unauth). uvicorn + cloudflared both active.

### Open / TODO
- ⚠️ **The 3 already-submitted trips will NOT bounce to Ted on their own.** Taipei101_HSK3_ZH,
  Taichung_HSK3_ZH and KaohsiungLotusPond_HSK12_ZH already have Gate-2 reports, so
  `pending_sessions` won't re-review them and no findings rows exist for them. To put them through
  the new triage loop, force a re-review per session (`claude_review.py --sid <sid>`), which
  bounces them to `ai_review`. **Awaiting dave's call** — it moves live trips out of his queue.
- **Nobody has an email set** (`users.email` is a new column) — until `manage.py set-email
  --username ted --email …`, Ted gets the in-app badge only.
- **Ted's 8 open bug reports (2nd→3rd tone TTS) are still unanswered.** Biggest item in the batch;
  a voice/model problem, not a review one.
- Deploy checklist when dave says go: pull BOTH repos on the laptop, restart uvicorn (schema
  migration: `users.email` + `auto_review_findings`), rebuild `frontend/dist`, confirm cloudflared
  is up (uvicorn alone = reviewers locked out).
- Dave's call, deferred: should a carried-forward `rejected` answer also survive a *verdict change*
  (warning→needs_human currently re-opens it)? And is compound rescue (老旧) too lenient at HSK3?

### Next steps
1. ~~Dave reviews; then deploy to the laptop + set Ted's email.~~ **Both done in session 2.**
2. Answer Ted's tone bug reports. → **root cause found in session 2; the email is drafted and
   with dave to send.**

---

## Session 2 — Ted's email, the re-review, and the blank-session incident (af11d9a LIVE)

### Goals
1. Set Ted's email so the AI-review findings actually reach him.
2. Re-review his three submitted (non-approved) trips so they bounce to him for triage.
3. Draft dave an email to Ted about the 喔 / tone problem.

### What I found — the 喔 story is NOT what it looked like
Dave's draft asked Ted "should I delete 喔 from the text?". **喔 is not in the script anywhere**
(grep of the whole Scripts tree: zero hits; staging json: zero hits). It is in
`localization_json.cur.Hans` ONLY — i.e. **Ted typed it in himself** on 2026-07-09 19:37–19:40,
in six fields of `Taipei101_HSK12_ZH`, and the app voiced exactly what he typed (`splice_meta`
mode=whole). He confirmed mid-session: *"I add another word to make it sound more natural."*

The six: s1 `楼喔` · s2 `楼喔` · s6 opt0/opt1 `这颗球` · s10 `人喔` · s12 opt0 `这个城市`.
**Every one of his 8 open tone bug reports (楼/球/人/城市) is on one of those six fields, and the
mis-voiced word always sits directly adjacent to the word he added.** So the hypothesis for the
email: the addition is what breaks the TTS tone, and reverting the six lines fixes all 8 reports
at once. Asked him to confirm the ORIGINAL audio says them correctly.
- Also: s7 has `堅固` — traditional 堅 in the **Simplified** box (should be 坚固). NOT a level
  issue: 强 (the original) and 柱子 (in the draft) are BOTH out of band for HSK1-2 already, so
  don't nag him about level here — I nearly did, and checked first.
- Gate-1 on that session: **7 hard blocks, all "zhuyin doesn't match the Simplified text"** —
  his Hans edits didn't carry into zhuyin/Hant. Six vanish if he reverts; s8's remains.
- ⚠️ **`sessions.revert()` does NOT clear `localization_json`** (sessions.py:2606 patches
  current_text/working_text/flag/audio only). On a _ZH field the 4-script block IS the voiced
  surface, so "Revert to original" leaves 喔 in the Hans box and Gate-1 still blocks. Reverting
  these has to be done by editing the Hans box by hand. **Unfixed — see BACKLOG.**

### Done
- `manage.py set-email --username ted --email <his PTS address>` (P0-0c). Notifier dry-run
  confirms the findings email now addresses him. `app_url` NOT added to `notifier_config.json`
  (sandbox blocked the live-config edit) → the email says "Open it here: the review app" with
  no link. **Still to do.**
- Re-reviewed the 3 submitted trips (P0-0b, dave's go): Taichung_HSK3 (1 finding),
  Taipei101_HSK3 (1), KaohsiungLotusPond_HSK12 (3). All three CAS'd to `ai_review`. **The new
  prompt is much cleaner — the old HSK-level noise is gone** (the ten 老旧 warnings didn't
  recur). 5 findings total: 3 are "English translation not updated after a Chinese edit"
  (→ Ted will defer those to the admin), 2 are stray leading newlines, 1 an unnatural comma.

### 🔥 The incident: opening an `ai_review` trip re-seeded a BLANK session
Dave reported "all the marked done have reverted... I can't see the AI's remark". **Nothing had
reverted.** The DB showed all 70/47/112 fields still `done`. What actually happened: opening
those trips from the list created **two brand-new blank sessions** (14:30, 14:34) — because
`create_or_resume`'s resume whitelist was `('in_review','submitted','changes_requested')` and
**`ai_review` was missing**. The blank session, being the NEWEST, then permanently shadowed
Ted's real one. Dave was looking at empty duplicates. (No work lost — but this would have
orphaned it silently.)

**Root cause is a class, not a line:** "which statuses are still live?" was hand-copied in TWO
places. So:
- **`backend/app/statuses.py` (NEW)** — the one enumeration. `ALL_STATUSES` /
  `TERMINAL_STATUSES=('approved',)` / `ACTIVE_STATUSES` (**derived**) / `EDITABLE_STATUSES`.
  Stdlib-only, because `auto_review_ingest` is imported by the cron runner without FastAPI.
- `sessions.create_or_resume` + `sessions._EDITABLE_STATUSES` + `structure._ACTIVE_STATUSES`
  all now derive from it. Adding a status can no longer be half-done.

### Two MORE bugs of the same class, found by the red-teams
1. **`approving` was missing from the resume list too** — same bug, second door. A crash (or a
   deploy restart) mid-approve strands a session in `approving`; the next open would seed a
   blank shadow. Resuming it is safe: it's not in `EDITABLE_STATUSES`, so it opens read-only.
2. **`structure._ACTIVE_STATUSES` was ALSO missing `ai_review`** — and this one is *worse* than
   the bug we started with. It's the guard that refuses an admin scene insert/remove/reorder
   while a live session exists (it desyncs `scene_index`es). A Gate-2-bounced trip slipped
   through it: an admin structural edit would have **silently misaligned every `field_edits` row
   of the reviewer's session**. Corruption, not just shadowing. (Tellingly, that list already
   had `approving` while the resume query didn't — which is what exposed door #1.)

### Also built (dave's ask)
- **Gate-2 findings now un-tick the ONE field each is about** (`auto_review_ingest.ingest`), so
  the reviewer's "all sections done" gate lands them on it. Every other field keeps its tick.
  **Coverage is NOT cleared** → they can re-tick without re-listening to the whole take.
- **The AI's remark + its three answer buttons now render INLINE next to each flagged field**
  (`SceneCard` → module-level `FieldFindings`, reusing `FindingCard`), not only in the summary
  panel. Findings state lifted to `ReviewPage` so both surfaces stay in step.
  - The summary panel and its "Go to scene N →" link **already existed** — they were invisible
    only because the blank session had no findings to show.

### Red-team (fable, then **opus** — dave switched the reviewer model mid-session)
Opus found a **real race the fable pass and I both missed**: the un-tick fired BEFORE the CAS
and unconditionally. If a report landed while the admin was mid-approve, the CAS correctly
no-op'd but the `done` flags were cleared anyway — rewriting reviewer state on a session they no
longer owned (and if approve then reverted to `submitted`, their ticks were simply gone). Now it
runs AFTER the CAS, gated on a status read-back being in `EDITABLE_STATUSES` (a recall that
raced the model leaves it editable, and the un-tick IS wanted there — hence read-back, not
rowcount). Opus also finished a `li > li` invalid-nesting fix the fable pass had only half-done.

### Verified
- 4/4 behavioural tests against a COPY of the live review.db: resume returns Ted's session (not
  a re-seed) · ingest un-ticks exactly the flagged fields and nothing else · coverage survives ·
  **a report landing during `approving` does NOT touch the done flags** (the Opus race).
- Gates: `npx tsc --noEmit`, `npm run lint`, `npm run build` all clean; backend imports clean
  incl. the cron runner's FastAPI-free import form.
- ON THE LAPTOP after deploy: `ACTIVE_STATUSES` + `structure._ACTIVE_STATUSES` both carry
  `ai_review`/`approving`; public app HTTP 200 serving `index-DV7gbZT1.js` (identical hash to
  the local build); `/api/findings/inbox` 401 unauth; uvicorn + cloudflared both active.

### Deployed (af11d9a — LIVE)
DB backed up to R2 first (`review-20260713-140735.db`), then pull → cleanup → `npm run build` →
`systemctl restart review-app.service`. Cleanup (`/tmp/cleanup_orphans.py`, dry-run first, and it
REFUSES to delete anything not pristine): deleted the 2 blank sessions after re-verifying 0 edits
/ 0 comments / 0 flags / 0 coverage / 0 findings, and un-ticked the 5 fields the existing
findings refer to (they were ingested before the fix). Post-state confirmed: each `ai_review`
session now has exactly as many un-ticked fields as it has open findings (1, 1, 3).

---

## Session 3 — revert() across all 3 languages, app_url, user guides (b3a0d36 LIVE)

### Goals (dave)
1. Do the user guides / quick reference need updating?
2. Add the app URL to the notifier.
3. **Fix "Revert to original" for Mandarin, and check Japanese + English too.**

### `revert()` was broken worse than "the box still shows 喔"
A field is written to **staging** if ANY of its text surfaces differs from its original
(`_field_has_edit`, sessions.py:2830): `current_text`, `source_text` (the editable English
sibling, written back by the `*En` writeback at ~3199), or the `_ZH` `localization_json` block.
`revert()` reset only `current_text`/`working_text` + the v0 mp3.

A Mandarin reviewer edits `localization.cur.*` and **never** `current_text` (`_zh_hans_for_tts`),
so revert was a **no-op exactly where it mattered**: the words stayed in the 4-script box, Gate-1
still blocked on the stale zhuyin, **and submit still wrote the edit to staging**.

**Checking JP/EN (dave's ask) found the shared gap:** `source_text` was never reset in ANY
language — so a reverted field could still push an English edit to staging.

`revert()` now also:
- `source_text` ← `original_source` (both NOT NULL, seeded from the same value → can't destroy
  content; only an explicit `update_source` can make them differ. Live DB: 0/389 rows differ).
- `_ZH`: `localization.cur` ← `orig`, **and re-baselines `working_hans` ← `orig.Hans`** —
  `working_hans` is the OLD text the next surgical CJK splice diffs against, and after a revert
  the working take IS the pristine v0 (which says `orig.Hans`); leaving it stale would splice at
  cut times for audio that no longer exists.
- **re-mirrors the restored take to R2** (red-team catch). NOT cosmetic: `resolve_audio_dir` can
  SEED a new session from the `review-audio` cache on a host with no local masters, so a stale
  mirror would hand it the edited audio as its "pristine" master.

### User guides — one was ACTIVELY WRONG, and the AI review was undocumented
- **Ted's guide still told him to "listen to V2 and V3 and pick the better voice"** — retired
  2026-07-02 (Mandarin is V3-only; the code is gone). Same stale text in the admin guide, the
  quick reference, and (red-team catch) `README.md`. All removed.
- **The whole Gate-2 triage flow was undocumented** and Ted is about to be asked to use it.
  Added to both reviewer guides (EN + zh/ja), the admin guide and the quick reference: the three
  buttons, the REQUIRED note on "Keep my version", answer-everything-then-resubmit.
- Added **"don't add words to the text to fix a mispronunciation — use Fix pronunciation…, which
  leaves the on-screen text unchanged"** (in zh + ja too). This is *exactly* the trap Ted fell
  into and is the single most valuable line in the whole doc change.
- Documented **Revert to original**.
- Guides render live from `docs/user-guides/` per request → **pull, no rebuild**.

### Red-team (/red-opus — dave switched the reviewer model this session)
No code defects. It found the R2 mirror gap (fixed above) and two doc inaccuracies I'd
introduced: the take-back button has **different labels on different pages**
(`AutoReviewPanel` "Take back without triage (admin)" vs `ChangesSummaryPage` "Take it back now
(unanswered items come to me)") and `README.md` still advertised the V2/V3 pick. It verified
every guide claim against the code (button labels, required-note rule, 409 `findings_open`,
inline rendering, "Apply suggested fix" only when verified+ZH → correctly absent from
Toshifumi's guide) and confirmed `dict(orig_loc)` is a sufficient copy (all 205 live
localization blocks are flat).

### Verified
- revert on ZH/JP/EN against a copy of the live DB, and again **ON THE LIVE HOST** against a DB
  copy using **Ted's real Taipei101 scene-1 field**: 喔 gone → `cur == orig` → `_field_has_edit`
  False → the corrected text reaches the FE payload. **Gate-1's zhuyin block clears itself.**
- Gates: tsc / lint / build clean; backend ast+import clean.
- `app_url` added to the laptop's `notifier_config.json` → the findings email now links to
  `https://review.dynamiclanguages.org/review/<sid>` (verified HTTP 200).
- Post-deploy: all four parts of the new `revert` present in the running process; public app
  200; `/help/quick` + `/help/guide` auth-gated (401); uvicorn + cloudflared active.

### Open / TODO
- **The email to Ted is drafted and pasted to dave but NOT sent** — dave sends it himself, with
  the audio attachments. It asks the decisive question: keep 喔/这颗/这个 (and we chase the TTS)
  or revert the six lines (and the tone bug likely dies with them)? **Revert now actually works,
  so "yes, delete them" is a one-click answer per field.**
- Ted's 8 tone bug reports remain formally `open` (the email answers them; close them after).
- The orphans' `work/` dirs (`sess_65e9b6d8ea3f`, `sess_887ef88d4e45`) are still on disk — seed
  copies of the masters only, harmless, delete whenever.
- Red-team nits NOT actioned (pre-existing): autosave-vs-revert race (typing then hitting Revert
  inside the 1 s debounce can re-save the edit — affects every editor, not just revert); the
  guides say "click any item to jump to that scene" but only the "Go to scene N →" link
  navigates (the app's own panel copy says the same, so guides + UI are at least consistent).
- `systemctl daemon-reload` still pending on the laptop (unit file changed on disk; harmless).

---

## End of day — 2026-07-13

### Shipped & LIVE on the laptop
| Commit | What |
|---|---|
| `2167ba6` | (session 1) Gate-1 deterministic HSK level check + Gate-2 findings → reviewer triage |
| `af11d9a` | (session 2) blank-session incident fix + status-vocabulary consolidation |
| `b3a0d36` | (session 3) `revert()` restores every text surface (ZH/JP/EN) + user-guide overhaul |

Also live: Ted's email set; the 3 submitted trips re-reviewed and bounced to him (`ai_review`);
`app_url` in the notifier; the 2 orphan sessions deleted.

### The theme of the day, worth carrying forward
Every serious bug found today was **the same shape: a hand-maintained list that a new feature
forgot to update.** `ai_review` was added in session 1 and *silently* missing from the resume
whitelist (blank sessions), from `structure._ACTIVE_STATUSES` (would have corrupted a live
session's scene indexes), and — the same class one level up — the user guides still described a
feature retired 6 weeks earlier, and `revert()` still only knew about the ONE text surface that
existed before the 4-script block and the English sibling were added.
**Lesson: when adding a status / a text surface / a feature, grep for every place the OLD set is
enumerated.** `backend/app/statuses.py` now makes that structurally impossible for statuses.
The remaining hand-maintained sets worth auditing next time something is added: the text
surfaces in `_field_has_edit` vs `revert` (now aligned), and the user guides.

### Where things stand with Ted
- He is **unblocked on nothing by us** — the ball is with him, via dave's email.
- The email (drafted, in session 2 + refined by dave) asks the one decisive question: keep
  喔/这颗/这个 (→ we chase the TTS) or revert the six lines (→ his 8 tone reports likely all die
  at once). **Revert now actually works, so "delete them" is one click per field.**
- His 3 re-reviewed trips are in his queue with 5 findings to answer (3 of them English →
  he'll defer those to dave).
- `Taipei101_HSK12_ZH` still has 7 Gate-1 hard blocks (stale zhuyin) + the 堅固 traditional-char
  typo — 6 of the 7 disappear if the six lines are reverted.

### Next session
1. **Ted's reply** → action it (revert the six lines, or chase the TTS), then close his 8 bug
   reports.
2. `Taipei101_HSK12_ZH`: fix 堅固 → 坚固 and the remaining scene-8 zhuyin, then it can be approved.
3. BACKLOG P1 #1/#2 (apply-fix button branch merge; R2 manifest coverage) — unchanged, still queued.
