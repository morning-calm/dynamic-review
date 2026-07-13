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

### Open / TODO
- **NOT DEPLOYED.** Nothing pushed to the laptop; awaiting dave's go.
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
1. Dave reviews; then deploy to the laptop + set Ted's email.
2. Answer Ted's tone bug reports.
