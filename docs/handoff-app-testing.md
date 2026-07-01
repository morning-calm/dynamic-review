# Handover — app state for thorough testing (prepared 2026-07-02)

State snapshot for the testing session that starts from here. Everything below was left
deliberately; nothing is mid-flight.

## Where the code is
- Branch **`feat/cjk-selection-tools`** (off `feat/cjk-surgical-splice`), **not pushed**.
  `a45592f` = the CJK selection tools + remove-pause + in-app help + updated user guides
  (see the commit message for the full inventory); a small docs/comment follow-up commit
  sits on top. `frontend/dist` is built and matches the source.
- All validation passed before commit: diff red-team 8/8 (unchanged cuts), selection
  red-team 26/26 (`research/cjk-aligner/redteam_cjk_selection.py`, no EL), 4 EL splices
  with per-char align-verify (`validate_cjk_selection.py`), in-app e2e on both demo
  sessions, and **dave's seam listen: all 4 pairs passed, no notes**.

## Servers
- **Everything is OFF** (uvicorn, cloudflared, vite). Start the app with:
  ```bash
  cd D:\Projects\WebApp\review-app
  REVIEW_APP_SERVE_FRONTEND=1 REVIEW_APP_COOKIE_SECURE=1 py -3.12 -m uvicorn \
    --app-dir backend app.main:app --host 127.0.0.1 --port 8000
  ```
  then open http://127.0.0.1:8000 (the SPA is served by the backend). `cloudflared tunnel
  run review-app` only if remote access is needed. Restart uvicorn via the Bash tool after
  backend edits (no --reload; PowerShell background launches die).

## Clean state guarantees (done 2026-07-02)
- **Sessions**: all three (`A._A. Milne…_EN`, `Tokyo_03_Beg_N4_JP`, `KaohsiungLotusPond_HSK3_ZH`)
  have **zero edits** — text = seed, no flags/comments/coverage/candidates/fallbacks,
  version history = v0 only, working audio = the pristine take (hash-verified).
- **ZH session keeps its V2 pick** (`preferred_version=v2`, model pinned
  `eleven_multilingual_v2`, voice `annasu`) — the pick is the precondition for the audio
  tools; switching to V3 re-promotes and is itself testable. Do **NOT delete** the ZH
  session: its masters are gone from disk, the `work/` copies are the only ZH audio.
- **Bug reports**: table + snapshot files purged (the one row was the feature-test report).
- **R2 (`review-audio`)**: all 18 test `*v<n>.mp3` / `*_fallback.mp3` keys deleted
  (bucket-wide, incl. old Bath/Battle/IyaValley/Bude test residue); canonical takes for the
  scenes touched by testing re-uploaded from the pristine local working files. No
  version/fallback keys remain anywhere in the bucket.
- **Users**: `admin`, `ted` (Mandarin), `toshifumi` (Japanese) — the throwaway e2e user was
  removed. Passwords unchanged.

## What to test (new since last session)
1. **JP + ZH selection tools** on SceneDesc: *Regenerate highlighted*, *…with alt text*,
   *Trim highlighted noise*, *Insert 1s pause*, *Remove 1s pause*. JP: highlight/caret in
   the **kana** (last) line — kanji-line selections get a 409 hint. ZH: highlight in the
   **Simplified (Hans)** box; the buttons under the player read that selection.
2. **Remove 1s pause** also on **English** trips (new for all languages).
3. **Wrong-line guards**: "Generate from edit" with only kanji / a non-Hans script edited →
   clear error naming the voiced line (button should also stay disabled in the UI).
4. **Help (?) button** in the top bar → Quick reference + user guide(s) in a new tab; the
   guide matches the signed-in user (reviewer accounts also get 中文/日本語).
5. **Bug reports** end-to-end (button → inbox → reply → status) — table is empty now.

## Expected (correct) conservative behaviours — not bugs
- A highlight sometimes **regenerates the whole narration instead of splicing** (toast says
  so): the ZH anchor floor is 0.90, and e.g. Kaohsiung scene 2's 那里有两座很高的楼 clause
  bails because its right ender 楼 aligns at 0.776 (its cuts were in real silence — the
  gate is deliberately conservative). The neighbouring 一座有老虎 clause splices surgically.
- *…with alt text* that can't splice → the part is flagged **edit-required** (the alt is
  never voiced as the whole field, never silently dropped).
- Trim/pause tools **refuse with a message** (409) rather than guess — e.g. no pause at the
  cursor, pause already at natural length, or the aligner venv missing.
- **Remove 1s pause** on a very long pause may remove slightly under 1 s per click (the
  silence run is measured within a bounded scan window) — click again for more.
- The MMS aligner venv must exist at `research/cjk-aligner/venv` (py3.12). Without it, CJK
  text-edit regens fall back to whole-regen and the selection/pause tools 409.

## Reference
- Guides live in `docs/user-guides/` and are served live at `/help/*` — editing the .md
  files updates the app without a rebuild.
- API shapes: `API_CONTRACT.md` (regenerate row + the audio-tool endpoint rows are current).
- Re-run validations any time:
  `cd backend && PYTHONIOENCODING=utf-8 py -3.12 ../research/cjk-aligner/redteam_cjk.py`
  (and `redteam_cjk_selection.py`; `validate_cjk_selection.py` spends EL credits).
