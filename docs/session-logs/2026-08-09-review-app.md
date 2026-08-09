# 2026-08-09 — review-app

## Session — CLEANER_VERSION 5: every language gets a numeral basis, reviewed and deployed

### Goals

Review dave's uncommitted `audio_core.py` + `test_number_clean_language.py` edits against the
two Scripts commits pushed today (`efccc236`, `62b5e37d`), answer three specific questions he
raised rather than take the edits on trust, commit, and deploy Scripts-pull-first.

### What changed upstream (context, both already on `origin/main` of `dynamic-content`)

* `efccc236` — every product language registered in `tts_number_clean._STRIPPERS`
  (was it/es/jp; now + en/fr/de/zh/ko). Also `clean_similarity` pre-expands the ORIGINAL
  before comparing, `difflib` autojunk off, Korean sino-year/acronym pre-expansion wired into
  `build_prompt`, and `korean_number_clean._YEAR_RE` fixed (it held a literal backspace byte,
  so `sino_year` had never fired).
* `62b5e37d` — **the prompt surface itself moved.** `PROMPT_EN` gained an ORDINAL rule (it had
  none), `PROMPT_ZH` gained decade / Japanese-era / route-number rules, `PROMPT_KO` short
  decimals, all three a completeness instruction. FR/DE/IT/ES/JP prompts unchanged.

### The three questions, answered

**1. Is the version bump sufficient, or do `_cleaned_orig` entries need explicit invalidation?**
Sufficient — no purge needed. `_cleaned_cache_key` mixes `CLEANER_VERSION` into a sha1 and
`_cleaned_orig` returns a cached entry **only** on `c["h"] == h`; any mismatch re-cleans and
overwrites in place. There is no read path that can serve a v4 entry (`cleaned_orig_json` has
exactly two touch points, `sessions.py:1473` and `:1480`), the blob can't grow because it is
keyed by field id, and `_cleaned_orig` is reached only from action endpoints — never from
`serialize_field` — so a resume does not fire a clean per field.

What the bump *cannot* do is make old audio say the new words. The baseline is an assertion
about what the WORKING TAKE says, so it is only true if the take was voiced by the same
cleaner. That exposure is real but was closed by inspection this time (see below) — and note
the bump is not merely hygiene: under the old guard, number-dense fields were being rejected
and cached `fallback=True`, which makes every highlight/alt regenerate on that field answer
*"Original text could not be cleaned reliably"*. Those fields un-wedge on re-clean.

**2. Is `_prose_survival` really unreachable now, and is keeping it still right?**
Unreachable against a **current** checkout, and the claim is narrower than "all eight
languages": zh/ko divert to their own harnesses before the retry loop, so the six that
actually reach `clean_accepted` are en/fr/de/it/es/jp. All six are registered — verified by
running it, not by reading `_STRIPPERS`:

```
reach clean_accepted: ['de','en','es','fr','it','jp']   unregistered among those: []
de/en/es/fr/it/jp -> clean_similarity called: True
```

Keeping it is right, and for a stronger reason than "a lagging laptop checkout" —
**the laptop's Scripts checkout was 71 commits behind when this session started**, so
`_prose_survival` was what production was running. It is also the only vocabulary-free arm,
i.e. the safety net if a language reaches `_LANG_CODES` before Scripts has a stripper for it
(the enumerated-set bug class this codebase keeps re-learning).

**3. Did anything else assume a language had no basis, or which arm is taken?**
No code did. Three doc surfaces did, all fixed here:
* `audio_core.py` — the accept/reject guard's own block comment still read *"only it/es/jp are
  registered … fr/de and en still have none"*. The previous pass updated the two comments
  above it and missed this one.
* `CLAUDE.md` — *"today it/jp"*, stale since es landed this morning.
* `BACKLOG.md` 0n.2 (*register fr/de/es upstream*) — closed, with the measured reason.

One gap the edit itself opened: replacing `assert _scripts_inventory_basis("fr", …) is None`
with a registration loop left **nothing pinning that we act on the basis**. The old line
proved deferral only by contrast, and with every language registered there is no negative case
left to write. Added `test_the_scripts_arm_is_the_one_taken` — stubs `clean_similarity` to
reject a clean our own arm accepts at recall 1.0, so a silent fall-back fails the test.

Also carried dave's `autojunk=False` through as-is; it is correct and matches what `cjk_splice`
had worked out independently.

### The re-voice: no reviewer is affected (checked, not assumed)

504 clips over 123 trips were re-voiced and re-uploaded to R2 today, which normally means an
`Scripts/refresh_review_app.py --clips` run (stale seed cache ⇒ new text over old audio).
**Not needed here — the intersection with the review queue is empty**, confirmed two
independent ways:

* `trips_to_review.json` (357 trips, generated 2026-08-05) ∩ the 124 re-voiced cids = **0**.
  The re-voiced trips are Scotland CEFR / Taiwan HSK / Busan TOPIK rungs that post-date the
  manifest and have not been exported to lanes 6/7 yet.
* `refresh_trips.py audit` on the laptop over all 124: **124 clear · 0 need a re-seed · 0
  HANDS OFF**, every one reporting `cache 0 files | no session`.

When those trips do enter the queue, the listing fills the seed cache from R2 with the NEW
bytes, so it is correct by construction. Nothing to clear, nothing to reseed.

⚠️ **But the headline number does not cover the review queue at all, and that is worth
carrying to the Scripts side.** `digits_reached_voice.py` scans `Logs/audio_queue.db`, which
holds only clips the `audio_engine` voiced — 230 trips, of which **0 are in the review queue**.
So "504 → 7" describes trips reviewers cannot see, and the 357 trips they ARE working on were
never scanned. That is unmeasured, not zero: those masters were voiced by earlier tooling
through the same word-ratio guard, and 167/357 were already known exposed by the 08-06
language-dispatch finding. Filed as a backlog item.

### Deploy — Scripts-pull-first, done in an idle window

Idle check first: the French reviewer's last session write was **130 minutes** earlier, and
presence 137 minutes. (I misread the first raw-epoch dump as 4 minutes and nearly held the
deploy on it; the second, computed properly, is the right figure.)

1. `backup_review_db.py backup` → `review-audio/_db-backups/review-20260809-140230.db`.
2. Laptop `~/Desktop/Server/Scripts` → `git pull --ff-only`, `0aee608` → **`62b5e37`** (71 commits).
3. **Import pre-flight while the old app was still serving** — the point of the ordering, and
   the check the 2026-07-09 `scene_ids` crash-loop taught us to run: all app modules +
   `jieba/pypinyin/opencc` import against the new checkout; `_STRIPPERS` shows all nine keys;
   `cleaner_status` still reported `3-cjk-cleaned` (old app code, as expected).
4. Laptop `~/Desktop/Server/review-app` → `git pull --ff-only`, `309cb20` → **`75ff39d`**
   (it was two behind — it had never picked up yesterday's `2731fa5` v4 bump either).
5. Backend suite **on the live host against the real Scripts checkout: 151 passed.**
6. `sudo -n /usr/bin/systemctl restart review-app.service`.

### Verified

* Workstation suite: **151 passed** (was 145; +6 from the new parametrized test).
* Live-host suite: **151 passed.**
* Startup line: `[startup] number-clean OK: deepseek-v4-flash v5-all-languages-numeral-basis —
  cleaning de, en, es, fr, it, jp, ko, zh`. No errors or warnings in the journal.
* `review-app.service` **active** AND `review-tunnel.service` **active** — both, per the house
  rule that a backend restart is not "the app is up".
* End-to-end through the tunnel: `https://review.dynamiclanguages.org/` → **200**,
  `/api/trips` unauthenticated → **401** (auth still enforced).

### Open / low-urgency

* ⚠ **The digit-voicing damage in the 357 queued trips is unmeasured** (see above). Scripts-side
  work: those clips' `spoken_text` is not in `audio_queue.db`, so the detector cannot see them
  and the app has no record of what its masters were voiced from. Filed as BACKLOG 0q.
* `systemctl` warns *"unit file … changed on disk, run daemon-reload"* on `review-app.service`.
  Pre-existing — nothing in today's pull touches a unit file — so the restart used the old unit
  definition, which is what has been running for days. Worth a `daemon-reload` next restart to
  see what the drift is.
* Registering fr/de/es upstream (BACKLOG 0n.2) is closed, but `_prose_survival` is deliberately
  NOT retired. If that ever gets revisited, the reason it stays is in the guard's block comment.

### Next steps

Nothing blocking. The next natural piece is the Scripts-side question above: decide whether to
scan the review queue's masters for digits-reached-voice, given they are the clips reviewers are
actually listening to.
