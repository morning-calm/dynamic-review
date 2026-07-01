# Review App — User Guides (SOPs)

Non-technical how-to guides for the people who use the review app. Keep these as the master
copies; adapt/translate as the app changes.

| Audience | Language | File |
|---|---|---|
| **Ted** — Mandarin translator/reviewer | English | [ted-mandarin-reviewer.en.md](ted-mandarin-reviewer.en.md) |
| **Ted** — Mandarin translator/reviewer | 中文 | [ted-mandarin-reviewer.zh.md](ted-mandarin-reviewer.zh.md) |
| **Toshifumi** — Japanese translator/reviewer | English | [toshifumi-japanese-reviewer.en.md](toshifumi-japanese-reviewer.en.md) |
| **Toshifumi** — Japanese translator/reviewer | 日本語 | [toshifumi-japanese-reviewer.ja.md](toshifumi-japanese-reviewer.ja.md) |
| **David** — Admin | English | [admin-guide.en.md](admin-guide.en.md) |

Related:
- Visual one-page quick reference (open in a browser): [quick-reference.html](quick-reference.html)
- Bug-report feature design doc (shipped): [../bug-reports-proposal.md](../bug-reports-proposal.md)

## Served in-app (the ? button)
These files are served live by the backend — the **?** button in the app's top bar opens them
in a new tab (`/help/quick` = the quick reference; `/help/guide` = the signed-in user's guide
in English; `/help/guide-native` = the 中文/日本語 version for reviewers). The guide is picked
by role/language: admin → admin guide, Japanese reviewer → Toshifumi's, Mandarin reviewer →
Ted's. **Editing these markdown files updates what users see** (no rebuild needed).

## Notes for whoever maintains these
- Ted reviews **Mandarin (`_ZH`)** trips → the 4-script block + V2/V3 voice pick.
- Toshifumi reviews **Japanese (`_JP`)** trips → the kanji/kana narration (the **kana** line is
  what's voiced).
- The **"Report a problem"** button is LIVE (in-app bug reports with snapshots + reply thread) —
  the guides describe it under "Report a problem".
- The highlight/selection audio tools (regenerate highlighted / alt text / trim noise /
  insert & remove pause) work in **all three languages** — JP highlights the kana line, ZH the
  Simplified (Hans) box.
- The guides deliberately contain **no** technical/development detail — translators only need the
  produce-perfect-text-and-audio workflow.
