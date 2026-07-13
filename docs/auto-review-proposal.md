# Auto-review proposal — replacing the admin review stage (2026-07-08)

**Goal:** when a reviewer submits a trip, the review-stage checks run automatically
(deterministic scripts + a Claude pass). A human only gets involved when something is
flagged, or when a manual edit is required. Dave stops being the bottleneck for clean
submissions.

**Proof of concept:** today's audit of Ted's four trips. 34 changed fields; every one had
stale sibling scripts; ~10 had traditional characters inside the Simplified line; one had an
English typo; four needed English meaning updates. All of it was found by exactly the two
mechanisms this proposal formalises — deterministic converters/aligners (`hsk_lib`) and
language-model judgment — and every proposed fix was machine-verified before being applied.

---

## 1. What the admin review actually does today (and what covers each part)

| Admin-review function | Automatable? | By what |
|---|---|---|
| Sibling consistency (Hans↔Hant↔zhuyin↔pinyin) | **Fully** | deterministic (`hsk_lib`) |
| Script purity (no 舊/颱/進 inside Hans, etc.) | **Fully** | deterministic |
| Format sanity (line counts, punctuation pairing, join2 shape) | **Fully** | deterministic |
| Vocabulary within the trip's level (HSK / CEFR) | **Mostly** | deterministic (word lists) with LLM fallback for context |
| Translation still matches after an edit (en ↔ target) | Judgment | **Claude** |
| Grammar / naturalness / register of the edited text | Judgment | **Claude** |
| Q&A logic (question answerable from scene; exactly one correct option; additionalAnswerKeys variants) | Judgment | **Claude** |
| Diff blast-radius (only expected fields touched; staging hasn't drifted since seed) | **Fully** | deterministic (already partly exists in `validate`) |
| Audio quality of regenerated takes | **No** | stays human — but it's already the REVIEWER's job (95% seek-proof done-gate). The admin listen was always a backstop, not the primary check. |

Key insight: the admin stage is ~80% mechanical consistency + ~20% linguistic judgment, and
**none of it is judgment only a human can make** except final accountability. Audio quality
is the one genuinely human sense-check, and the reviewer already owns it.

## 2. Architecture — two gates, one report

```
reviewer submits
      │
      ▼
┌─ Gate 1: deterministic validators ────────────── runs in-process at submit, <1s ─┐
│  zh: to_simplified(Hant)==Hans · zhuyin_to_pinyin full-confirm · script purity   │
│  all: sibling-changed-together · format/punct · level vocab · diff blast-radius  │
│  HARD failures block submit with a 409 (like the pinyin guard) — reviewer fixes  │
│  immediately while they're still in the session.                                 │
└───────────────────────────────────────────────────────────────────────────────────┘
      │ pass (or soft warnings only)
      ▼
┌─ Gate 2: Claude review ──────────────── async, minutes, triggered by submit ─────┐
│  Input: the session diff JSON (orig→cur per field, all scripts + en), trip level, │
│  scene context. Output: structured verdict per field:                             │
│    ok | warning(reason) | needs_human(reason) [+ suggested_fix]                   │
│  Suggested fixes are PROPOSALS: any zh fix must pass the Gate-1 verifiers before  │
│  it's even shown; nothing is ever auto-applied to text without a human click.     │
└───────────────────────────────────────────────────────────────────────────────────┘
      │
      ▼
  auto-review report stored per session (new table) + shown on ChangesSummaryPage
      │
      ├─ all fields `ok`            → trip enters "ready to approve" (Phase 3: auto-approve)
      └─ any warning / needs_human  → email dave (notifier immediate-kind), manual queue
```

### Why both gates, in this order
- Deterministic checks are free, instant, and **never wrong** — they belong at submit time
  so the reviewer fixes mechanical problems themselves (this alone would have caught 30 of
  today's 34 findings before Ted ever submitted).
- Claude catches what scripts can't (meaning drift, "wind" vs "typhoon", a question no
  longer answerable) — but LLM output is a *flag*, never an unreviewed write. The pattern
  that worked today: **Claude proposes → deterministic code verifies → human confirms.**

## 3. Implementation plan

### Phase 1 — deterministic validator (a week of evenings, no new infra)
New module `backend/app/auto_checks.py`, called from `submit()` (and exposed as
`GET /api/sessions/{sid}/auto-checks` so the FE can show live status pre-submit):
- **zh_sibling_check**: for every changed localization field — `to_simplified(Hant)==Hans`,
  `zhuyin_to_pinyin(zhuyin, Hans)` must fully confirm, script purity both directions,
  en non-empty. (All functions exist in `hsk_lib`; the verify script from today is the
  prototype.)
- **partial_change_check**: some scripts changed, siblings identical → warning (mirrors the
  new UI hint, but enforced at submit).
- **format_check**: paired punctuation, no double spaces, join2 shape for the Trip doc,
  numbers/spelling hints for additionalAnswerKeys (the existing orange notes, made systematic).
- **level_vocab_check** (zh first): jieba-segment the changed Hans, look up HSK level per
  word, flag words above the trip level. English CEFR later via the existing CEFR word lists.
- Severity model: `block` (submit 409s, reviewer must fix) vs `warn` (recorded in the report).
  Start conservative: only the mechanically-certain checks block.

### Phase 2 — Claude reviewer (the new piece)
- `scripts/claude_review.py`: dumps the submitted session's diff (the exact JSON I audited
  today), calls Claude via the Agent SDK / `claude -p` headless with a fixed review prompt +
  structured-output schema, writes `auto_review` rows (per-field verdict + reasons +
  suggested fixes), exits.
- **Trigger:** the laptop cron pattern that already works — a `*/5` watcher (or a hook in
  `submit()` that touches a queue file) runs the review for any newly-submitted session
  without a report. No always-on service needed.
- **Where it runs:** the laptop (has the DB) calling the API with an API key, or the
  workstation over SSH like today — laptop-local is simpler.
- **Surfacing:** ChangesSummaryPage gets an "Auto-review" panel per field (ok/warn/flag +
  reason + proposed fix with an "apply" button that goes through the normal
  `update_localization` path). The activity notifier emails dave a one-line verdict per
  submit: "Taipei101 submitted — auto-review: 6 ok, 2 flagged (scene 6 en meaning, scene 9
  format)". Immediate-kind, so it arrives with the finish email he already gets.
- **Cost/latency:** one review ≈ one model call over a few KB of diff — seconds, pennies.

### Phase 3 — auto-approve (opt-in, after trust is earned)
- Config flag `AUTO_APPROVE_CLEAN=1`: a session whose Gate-1 is clean AND whose Claude
  report is all-`ok` is approved automatically (the existing `approve()` path, attributed
  `approved_by='auto'`), with the notifier email as the audit trail. Anything else waits in
  the manual queue exactly as now.
- Rollout: run Phases 1–2 in shadow mode for a few trips (reports generated, dave still
  approves manually, compare his decisions to the reports). Flip auto-approve on when the
  reports have caught everything he would have.

## 4. Guard-rails (what keeps this safe)
1. **Nothing writes to staging without either a human click or an all-clean report under an
   explicit opt-in flag.** The LLM never writes text directly — suggested fixes go through
   the same autosave endpoints a human uses, post-verification.
2. **Deterministic verification of LLM output**: any suggested zh fix must pass the same
   `hsk_lib` alignment checks before display (today's workflow, encoded).
3. **Audio stays human**: the reviewer's seek-proof done-gate is untouched; auto-review is
   text-only. (An ASR gross-content cross-check is possible but explicitly NOT for
   truncation detection — whisper hallucinates completions; see splice-end-cutoff analysis.)
4. **Fail-open to manual**: if the Claude runner errors or times out, the session simply sits
   in the manual queue as today. Auto-review can only reduce dave's work, never lose a trip.
5. **Everything logged**: reports are rows tied to the session; the approve records which
   report version it acted on.

## 5. What I'd build first
Phase 1 `zh_sibling_check` + `partial_change_check` wired into submit (would have stopped
today's entire incident class at the source), then the Phase 2 runner in shadow mode on the
next Ted submission. Phase 3 only after dave has seen a few shadow reports he agrees with.

---

# Addendum — 2026-07-13: level checking moves to Gate 1; Gate 2 goes to the REVIEWER

Two changes, both driven by an audit of Ted's six Mandarin scripts (Taichung / Taipei101 /
KaohsiungLotusPond, HSK12 + HSK3). Full analysis: `docs/reviews/2026-07-13-ted-six-scripts-collated.md`.

## A. "Above level" is now a lookup, not an opinion

**What we found.** Gate 2's prompt asked Sonnet to judge vocabulary level. Across the six
scripts it made **7 level judgments: 3 correct, 4 wrong** — and it *missed* 5 out-of-band
words entirely, 4 of which it had explicitly cleared. Concretely, it flagged 离开 (**HSK2**)
and 保持 (**HSK3**) as above-level, invented band numbers ("保持≈HSK5"), and cleared 旧 as
"HSK2" when it is **HSK3** — out of band for an HSK12 trip. Meanwhile the content pipeline
has shipped a real checker all along (`level_check.py`: jieba + `hsk_vocab.xlsx`).

**What we did.**
- New `backend/app/zh_level.py` — the pipeline's vocabulary rules, in the review app. Wired
  into Gate 1 (`auto_checks._level_issue`) as a **warn, never a block**: an out-of-band word
  is a legitimate i+1 authoring choice, it just has to be a *conscious* one.
- It reports **only what the edit introduced** (diff of orig→cur out-of-band sets). The
  approved draft's own i+1 words are not the reviewer's to answer for, and nagging about
  them would be noise they can't act on.
- **LEVEL was deleted from the Gate-2 prompt.** Don't put it back — the comment in
  `claude_review.py` says why.

**⚠️ Why we ship a JSON snapshot instead of importing `level_check.py`.** `*.xlsx` is
**gitignored** in the dynamic-content repo, so `hsk_vocab.xlsx` exists ONLY on the machine
that built it. The live laptop has the Scripts checkout but **not the xlsx** — an import
would work on the workstation and silently fail in production (exactly the laptop-env failure
class that stripped pinyin on 2026-07-08). So `scripts/export_hsk_vocab.py` snapshots the
reference to `backend/app/data/hsk_vocab.json` (committed), and `zh_level` reads that +
jieba (which the live venv does have). **Re-run the export whenever the HSK reference or
hsk_config's proper-noun lists change.**

`zh_level` mirrors `level_check.Checker` exactly — same POS content-word filter, proper-noun
substring exemption, single-char rule, and **compound rescue** (an unlisted multi-char word
is in band if every hanzi appears in some in-band word). Compound rescue is what passes 老旧,
台风, 月台 — worth knowing, because it means the answer to "is 老旧 above HSK3?" is *"it is in
band by the same rule the drafts were written to."* If that rule is ever judged too lenient,
that's a pipeline-wide policy change, not a review-app one.

## B. Gate 2's findings go back to the REVIEWER (no longer shadow-only)

Shadow mode put the LLM's findings in front of the **admin**, which is the wrong person: the
reviewer made the edit, speaks the language, and is the one who can answer. New workflow
(dave, 2026-07-13):

```
submit ──► Gate 2 (cron) ──► any warning/needs_human?
                              │
                    no ───────┴──────► stays 'submitted' → admin approves (unchanged)
                    yes ─► findings created, session CAS'd 'submitted' → 'ai_review'
                           ├─ back in the REVIEWER's queue, EDITABLE again
                           ├─ admin approve is blocked (approve() only claims 'submitted')
                           └─ reviewer answers EVERY finding:
                                 resolved — actioned it (often via "Apply suggested fix")
                                 rejected — keeps their version; NOTE REQUIRED (admin reads it)
                                 deferred — it's about the ENGLISH/source → the admin's call
                           re-submit (blocked by 409 `findings_open` until all answered)
                              └──► 'submitted' → admin sees each answer + note on /admin/{sid}
```

- **Escape hatch:** an admin can `POST /sessions/{sid}/findings/skip` ("take it back now") —
  open findings are marked `deferred` to the admin and the session returns to `submitted`.
  This is what guarantees the gate can never wedge a trip.
- **Notification:** in-app badge (`GET /findings/inbox`) + an email to the submitting reviewer
  (`activity_notifier.notify_reviewer_findings`, watermarked, sent immediately rather than
  batched behind dave's 1/hour digest gate). A reviewer with no `users.email` is skipped
  silently — set one with `backend/manage.py set-email`.
- Still true: the LLM **never** writes text or touches staging. Suggested fixes go through the
  same autosave path a human uses, and only if `hsk_lib` verified them.
