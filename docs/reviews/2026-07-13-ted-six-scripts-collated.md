# Ted's six Mandarin scripts — all review comments, collated

**Compiled:** 2026-07-13 · **Source:** live `review.db` on the laptop host (sessions,
`auto_reviews`, `field_edits.comment`, `bug_reports`)

Ted edited six scripts: three Taiwan trips at two HSK levels each.

| Trip | Session | Status | Auto-review verdicts |
|---|---|---|---|
| KaohsiungLotusPond_HSK3_ZH | `sess_5bc56203b40a` | approved (07-06) | *none — reviewed before the pipeline existed* |
| KaohsiungLotusPond_HSK12_ZH | `sess_4243b6da1f26` | **submitted, awaiting your approval** | 11 ok · 4 warning · 1 needs-human |
| Taichung_HSK12_ZH | `sess_16e3026e0980` | approved (07-09) | 7 ok · 1 warning · 0 needs-human |
| Taichung_HSK3_ZH | `sess_0ef71bcd3373` | **submitted, awaiting your approval** | 0 ok · 10 warning · 1 needs-human |
| Taipei101_HSK3_ZH | `sess_db4ac31ff3ff` | **submitted, awaiting your approval** | 2 ok · 4 warning · 2 needs-human |
| Taipei101_HSK12_ZH | `sess_67d43aae2c03` | still in review (Ted last touched 07-11) | *no report — reports are only generated at submit* |

---

## Part 1 — Where these comments come from (three different sources)

There are **three independent commentary streams**, and it matters which is which because
they have very different reliability.

### 1. Gate 1 — deterministic machine checks (`backend/app/auto_checks.py`)
Pure code, no LLM, runs at submit. It can **block** a submit. It checks only *mechanics*:

- traditional characters leaking into the Simplified box (block)
- Hant not corresponding to Hans (block; punctuation-only difference = warn)
- zhuyin not aligning syllable-by-syllable with Hans, via `hsk_lib.zhuyin_to_pinyin` (block)
- English translation emptied (block)
- one script edited but its siblings left stale (warn) — the failure that shipped a bad trip on 07-08
- unbalanced brackets/quotes, double spaces, stray whitespace (warn)

**Gate 1 does not look at vocabulary or level at all.**

### 2. Gate 2 — the LLM judge (`scripts/claude_review.py`)
This is **Claude Sonnet**, called headless (`claude -p`) from cron every 5 minutes on the
laptop, on every *submitted* session. **Not Gemini.** (Gemini 2.5 Flash is in this codebase,
but only as the number/unit speller that preprocesses text before ElevenLabs TTS in
`audio_core.clean_text` — it never sees or comments on review edits.)

It is **shadow mode**: it writes a report and a one-line verdict into your notifier email;
it never changes status, text, or staging. Its per-field verdicts are `ok` / `warning` /
`needs_human`. It is asked to judge exactly four things: **meaning equivalence** across
Hans/Hant/zhuyin/en, **language quality**, **level-appropriateness**, and **Q&A logic**.

> ### ⚠️ Answering your question directly: the "word above level" comments
>
> **Every "above HSK3" / "above HSK1-2" comment in these reports is Claude Sonnet's
> *opinion*, not a dictionary lookup.** There is no systematic vocabulary check anywhere in
> the review app. The prompt just tells the model "trip level is HSK3 — flag vocabulary or
> grammar clearly above the level" and it judges from its own knowledge of the HSK bands.
>
> So they are **plausible but unverified**. Some are clearly right (保持/平衡 really are
> HSK5-ish). Some are soft judgement calls you may disagree with (离开 flagged as too hard for
> HSK1-2; 铁轨 in a *title label*). The model even assigns specific bands ("台风≈HSK4,
> 保持/平衡≈HSK5") with a confidence the method doesn't actually support.
>
> **But a real systematic checker already exists** — in the *content* pipeline, not here:
> `Scripts/Research and Writing/HSK Mandarin/stages/level_check.py`. It segments with jieba,
> matches every content word against `hsk_vocab.xlsx`, honours the trip's band ceiling
> (`IN_BAND_LEVELS`), skips proper nouns and function words, and lists **every** out-of-band
> word with its scene and whether it was declared as intentional i+1. It runs on `draft.json`
> at drafting time — so it has **never seen Ted's post-edit text**. That's the gap: a reviewer
> can silently raise a trip's level after the only real level check has already run.
>
> **Recommendation:** wire that vocab list into Gate 1 so "out-of-band word" becomes a
> deterministic warning with a named HSK level, and let Sonnet stop guessing at it. That is a
> small, self-contained change (the `hsk_lib` import path is already in `auto_checks.py`).

Two more caveats on the Sonnet reports:

- **Suggested fixes are post-verified, and all of these failed.** Every `suggested_fix` in
  these four reports carries `verified: false`, i.e. `to_simplified(Hant) == Hans` and/or the
  zhuyin alignment did **not** confirm. Treat them as prose suggestions, never paste them in
  as-is. (Several are only partial — e.g. a Hans fix with no matching Hant/zhuyin — which is
  itself why verification fails.)
- Nothing it says has ever been auto-applied.

### 3. Ted himself — in-app field comments and bug reports
These are human, and the most actionable of the three. Collated in Part 3.

---

## Part 2 — The LLM (Sonnet) comments, by trip

### KaohsiungLotusPond_HSK12_ZH — *submitted, 11 ok / 4 warn / 1 flag*
> **Summary:** "Most edits are solid fixes (several correct long-standing zh/en translation
> mismatches and grammar improvements); flag one above-level vocabulary swap, two stray
> leading-newline artifacts, and one subtle aspect/tense drift."

- **needs_human · scene 5 · SceneDesc** — measure-word fix is good (一个大龙 → 一只/一隻, the
  correct classifier for 龙/虎), **but 巨 ("giant") is above HSK1-2**. Also the unchanged en
  ("big dragon") no longer precisely matches 巨龙 ("giant dragon").
  *Suggested (unverified):* `这里有一只大龙，还有一只大老虎。` / `這裡有一隻大龍，還有一隻大老虎。`
- **warning · scene 10 · titleKey** — **stray leading newline** in Hans (`\n池塘的景色`), not
  present in Hant. Paste artifact. Meaning shift 看水 → 池塘的景色 is fine.
- **warning · scene 18 · questionKey** — **stray leading newline** in en (`\nWhat is in the
  painting?`). Translation otherwise correctly fixed.
- **warning · scene 24 · SceneDesc** — 走 → 离开: same meaning, but 离开 may sit above a strict
  HSK1-2 bar. *(LLM judgement — see caveat above.)*
- **warning · scene 24 · questionKey** — 我们做什么？ → 我们**在**做什么？ adds the progressive
  marker ("what are we doing *right now*"), but en was left as the general "What do we do?" —
  **tense/aspect mismatch**. Suggested en: "What are we doing?"
- **Genuinely good (ok):** scenes 9, 13, 18, 19 all *corrected long-standing en↔zh mismatches*
  in the source content (en said "staircase"/"tunnel"/"pond"/"houses on the water" where the
  Chinese said 树/人/花). Scene 16's 女的 → 女人 / 男的 → 男人 is applied consistently across the
  SceneDesc and all three options.

### Taichung_HSK3_ZH — *submitted, 0 ok / 10 warn / 1 flag* ← **the noisiest report**
> **Summary:** "Systematic 老→老旧(的) substitution across scenes 13/14/16/18 introduces
> vocabulary above HSK3, plus a real meaning drift in scene 18."

- **needs_human · scene 18 · SceneDesc** — **real meaning drift**: Hans/Hant/zhuyin changed
  很有意思 ("interesting") → 很好玩 ("fun"), but **en still says "very interesting."** This one
  is a genuine consistency bug, independent of any level opinion. Ted's own review note on
  this session, tellingly, reads *"great fun interesting"*.
- **warning ×9 · scenes 13, 14, 16, 18** (SceneDesc, questionKey, questionOption 0/1/2) —
  **all ten warnings are the same single issue**: Ted replaced 老 with 老旧 / 老旧的 throughout,
  and Sonnet judges 老旧 above HSK3. Two secondary observations worth keeping even if you
  reject the level call:
  - **inconsistent form**: 老旧的 in scenes 13/16/18-question, but 老旧 (no 的) in scene 14 and
    in scene 18's own SceneDesc — the same trip uses both.
  - Q&A logic and Hans/Hant/zhuyin/en consistency are fine everywhere else.

**Decision needed:** is 老旧 acceptable at HSK3? If yes, 10 of the 11 comments evaporate and
only the scene-18 en drift remains. `level_check.py` would settle this in one run.

### Taipei101_HSK3_ZH — *submitted, 2 ok / 4 warn / 2 flags*
- **needs_human · scene 6 · SceneDesc** — the rewrite introduces **台风, 移动, 保持, 平衡**
  (Sonnet: 台风≈HSK4, 保持/平衡≈HSK5) replacing the simpler 刮风 / 这个球会动. Also the **measure
  word flips from 个 to 颗 for the same ball** referenced earlier in the trip — an internal
  inconsistency that is *not* a level opinion and stands on its own.
- **needs_human · scene 9 · SceneDesc** — `台北101有，一百零一层地上，和5层地下。` has
  **ungrammatical commas** (splitting 有 from its object, and before 和); likely to force an
  unnatural TTS pause. Also **mixed numeral style**: 101 spelled out as 一百零一 but 5 left as a
  digit. *Suggested:* `台北101有一百零一层地上和5层地下。`
- **warning · scene 4 · SceneDesc** — **stale sibling**: Hans and zhuyin updated to the
  spelled-out 五百零八公尺, but **Hant left as `508米`**. (This is the class of error Gate 1
  catches — worth checking why it only warned.)
- **warning · scene 7 · SceneDesc** — 坏 (HSK1) → 损坏 (Sonnet: HSK4). Meaning unchanged.
- **warning · scene 8 · SceneDesc + questionKey** — 攀岩的人 → **攀岩家**; the 家 suffix reads
  non-standard for this activity, conventional term is 攀岩者. Meaning/en still fine.
- **ok:** scene 11's 烟花→烟火 and 地面→现场 swaps, applied consistently across all four forms.

### Taichung_HSK12_ZH — *approved, 7 ok / 1 warn / 0 flags* (cleanest of the six)
- **warning · scene 12 · titleKey** — 铁轨 ("railway track") above core HSK1-2 *(level
  opinion)*. Note the model itself observes the new Hans/Hant 铁轨上面 **matches the existing en
  "Above the Tracks" better than the original 高路 did** — so the edit improved consistency.
- Everything else clean: 站台→月台 (correct Taiwan localisation), 午饭→午餐, and a systematic
  在 + tense edit across all three options with en updated to match.

---

## Part 3 — Ted's own comments and open bug reports

### Field comments Ted left
| Trip | Scene / field | Comment | Flag |
|---|---|---|---|
| Kaohsiung_HSK12 | 1 · questionKey | "The pronounce generate is not that accurate." | done |
| Taipei101_HSK12 | 1 · SceneDesc | "Please delete '喔'" | **edit required** |
| Taipei101_HSK12 | 2 · SceneDesc | "Please delete '喔'" | **edit required** |
| Taipei101_HSK12 | 6 · SceneDesc | "'球' this sound generate is not accurate." | done |
| Taipei101_HSK12 | 6 · questionOption 0 & 1 | "Please delete '这颗'" | **edit required** |
| Taipei101_HSK12 | 10 · SceneDesc | "Please delete '喔'" | **edit required** |
| Taipei101_HSK12 | 12 · questionOption 0 | "Please delete '这个'" | **edit required** |

Six `edit_required` flags, all on **Taipei101_HSK12_ZH** — the session he has *not* submitted.
He is blocked on them, and they're all the same two asks: **delete the filler particle 喔**,
and **delete the demonstrative 这颗/这个**.

Also on scene 6 the app itself appended: *"[auto] Couldn't splice the alt text cleanly at the
highlighted spot — try a highlight within one clause, or use Create new."*

### Open bug reports — 8, all on Taipei101_HSK12_ZH, all still `open` (filed 07-09)
Every one is the **same underlying TTS defect: ElevenLabs renders 2nd-tone syllables as 3rd tone.**

- scenes 1, 2 (SceneDesc) + scene 2 (option 0): **"'楼' … supposed to sound like 2nd tone, but it's 3rd tone now."**
- scene 6 (SceneDesc, options 0 & 1): same for **'球'** — and on the options, *"it can't generate the '球' whole sound."*
- scene 10 (SceneDesc): same for **'人'**
- scene 12 (option 0): *"It can't generate '城市' the whole sound."*

**Nobody has replied to any of them** (`bug_report_messages` is empty). Ted's four earlier
reports on Taipei101_HSK3 ("Can't mark done.") were resolved.

---

## Part 4 — What actually needs your decision

1. **The 2nd→3rd tone TTS bug (8 open reports).** This is the biggest single item and it is a
   *voice/model* problem, not a review problem. 楼 / 球 / 人 are all rising-tone syllables being
   voiced as dipping. It's blocking Ted on Taipei101_HSK12 and he's had no reply for four days.
2. **Is 老旧 acceptable at HSK3?** One yes/no that clears 10 of the 11 Taichung_HSK3 comments.
3. **Three genuine consistency defects**, all independent of any LLM level opinion:
   - Taichung_HSK3 scene 18 — Hans says 好玩, en still says "interesting"
   - Taipei101_HSK3 scene 4 — Hant still `508米` while Hans/zhuyin say 五百零八公尺
   - Taipei101_HSK3 scene 6 — measure word 个 → 颗 for the same ball
4. **Two stray leading newlines** in Kaohsiung_HSK12 (scene 10 Hans, scene 18 en) — paste
   artifacts that should never have passed Gate 1's whitespace check. Worth investigating why
   they were only warnings.
5. **Ship the deterministic level check.** Port `level_check.py`'s vocab matching into
   `auto_checks.py` so "above level" stops being an LLM guess. **See Part 5 — it was run, and
   Sonnet's level calls were wrong about as often as they were right.**

---

## Part 5 — Deterministic level check vs. Sonnet (run 2026-07-13)

**Method:** ran the pipeline's own `level_check.py` vocabulary engine (jieba segmentation +
`hsk_vocab.xlsx`, the trip's real band ceiling, proper nouns and function words excluded) over
all 59 changed fields, diffing the out-of-band word set of the **original** Hans against the
**edited** Hans. Anything in the "introduced" column is a word **Ted's edit newly added** that
sits outside the trip's band. Pre-existing out-of-band words (老虎, 雕像, 地震, 阻尼器, 攀岩…)
are ignored — those were in the approved draft and are almost certainly declared `i1_words`.

### Scorecard: Sonnet made 7 level judgments. 3 held up. 4 did not.

| # | Sonnet's claim | Deterministic verdict |
|---|---|---|
| 1 | Kaohsiung_HSK12 s5: **巨** above HSK1-2 | ✅ **Correct** — 巨龙 out of band |
| 2 | Taichung_HSK12 s12: **铁轨** above HSK1-2 | ✅ **Correct** — out of band |
| 3 | Taipei101_HSK3 s7: **损坏** above HSK3 ("HSK4") | ✅ **Correct** — out of band (the "HSK4" label is invented; it isn't in the list at all) |
| 4 | Kaohsiung_HSK12 s24: **离开** "somewhat more advanced… may sit above HSK1-2" | ❌ **Wrong** — 离开 is **HSK2**. Squarely in band |
| 5 | Taipei101_HSK3 s6: **台风≈HSK4, 保持/平衡≈HSK5, 移动** | ⚠️ **Half right** — 移动 ✅ and 平衡 ✅ are out of band, but **保持 is HSK3 (in band)** — not HSK5 — and **台风 passes**. The confident band numbers are fabricated |
| 6 | Taichung_HSK3 ×10: **老旧 / 老旧的** above HSK3 | ❌ **Not confirmed** — passes in band (see below). **This is 10 of the 11 warnings on that trip** |
| 7 | Taichung_HSK12 s13: *"旧 is HSK2… no level issue"* (an explicit **clearance**) | ❌ **Wrong, and dangerous** — **旧 is HSK3**, i.e. out of band for an HSK12 trip. Sonnet waved through the exact thing it was there to catch |

### And it missed five out-of-band words entirely — four of which it explicitly cleared

| Where | Word Ted introduced | Sonnet said |
|---|---|---|
| Taichung_HSK12 s11 SceneDesc | **饮料店** (out of band) | *"Vocabulary (饮料店, 卖) is within HSK1-2"* — flatly false |
| Taichung_HSK12 s13 SceneDesc | **旧** (HSK3 → out at HSK12) | *"旧 is HSK2… no meaning drift"* — false |
| Taichung_HSK12 s16 titleKey | **月台**, **旧** (out) | *"Comparable complexity… not a new level issue"* — false |
| Kaohsiung_HSK12 s10 titleKey | **池塘** (out), **景色** (HSK3 → out at HSK12) | only flagged the stray newline; called the meaning shift "fine" |
| Taipei101_HSK12 s6 options | **颗球** (segmentation artifact of 这颗球) | *(session unsubmitted — never reviewed)* |

### The 老旧 question, answered honestly

老旧 is **not in the HSK wordlist at all**. It passes the deterministic check by the pipeline's
**compound-rescue rule**: an unlisted multi-character word is treated as in band if *every one
of its characters* appears in an in-band word (老 = HSK1, 旧 = HSK3). So the correct statement
is not "老旧 is an HSK3 word" — it's **"老旧 is in band by the same rule the original drafts
were written to."** By the pipeline's own standard, Ted's edit is acceptable and those 10
warnings are noise. If you think compound rescue is too lenient, that's a policy argument to
have about the rule — but then it applies to the drafts too, not just to Ted.

(Same rule is what passes 台风, 月台, 站台 and 女的.)

### Bonus find — a real defect Sonnet never saw

**Taipei101_HSK12 scene 7 SceneDesc contains 堅固 — traditional characters sitting inside the
Simplified box.** That's a Gate-1 **block**, and it will fire the moment Ted submits that
session (he hasn't yet). Worth telling him now rather than letting the submit bounce.

### What this means

The deterministic checker is **strictly better than the LLM at this job** and it already
exists. It is also *stricter* — it flagged out-of-band words in the two **approved** trips
(Taichung_HSK12, which is already through). Recommended wiring:

- Add an "out-of-band word introduced by this edit" check to `auto_checks.py` at **warn**
  severity (never block — the pipeline's own checker "NEVER blocks", and i+1 words are a
  legitimate authoring choice; it just has to be a *conscious* one).
- Compute it as a **diff** (orig vs cur), exactly as above, so reviewers aren't nagged about
  out-of-band vocabulary that was already in the approved draft.
- Then **delete item 3 (LEVEL) from the Sonnet prompt** and let it keep doing what it's
  genuinely good at: meaning drift, stale siblings, punctuation, Q&A logic — where its hits in
  this batch (好玩/"interesting", `508米`, the 个→颗 measure word, the comma splice) were all real.
