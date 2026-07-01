# Bug reports — design options & decision

Status: **built** (button → snapshot → admin inbox → reply thread → nav badge). This doc records
the options considered and what was chosen, so the deferred pieces can be picked up later.

## What was built (the chosen stack)
- **Capture:** a "Report a problem" button in each audio field's control row → a modal (free text,
  any language). On submit the backend snapshots the field's current text + `localization` and copies
  the working/candidate mp3 into `work/bug_reports/{id}/`.
- **Storage:** `bug_reports` + `bug_report_messages` tables in `review.db` (source of truth).
- **Triage:** `/bugs` inbox — admin sees all, a reviewer sees only their own; status
  open/investigating/resolved (admin-only); a reply thread either side can post to.
- **Notify (in-app):** a nav badge — admin = open count, reviewer = unread admin replies.
- **Notify (Claude):** `scripts/check_bug_reports.py` prints open reports (run at session start /
  schedulable; exit 1 if any).

## Options considered

**Storage** — DB table *(chosen)* vs GitHub issues vs Asana tasks. The table keeps the audio/text
snapshot next to the session and needs no external account; GitHub/Asana can mirror it later for
tracking.

**How Claude notices** — a scheduled routine that polls + pings *(recommended follow-on)* vs the
`check_bug_reports.py` sweep at session start *(chosen for now)* vs GitHub-issue sweep. A **cloud**
routine can't read the local `review.db`, so any automated push must run locally or go through the
backend.

**How the admin is informed** — in-app badge *(chosen)* vs email vs Asana. The badge is
self-contained; email/Asana reach a phone.

**Reply loop** — in-app thread *(chosen)* vs email round-trip. The thread keeps everything in the app
with no email addresses/PII; the reviewer replies in-app and the admin can copy the thread to Claude
for the next fix.

## Deferred (needs a decision to build)
- **External push to the admin** (email digest / Asana task on a new report). The **backend** can't use
  Claude's Gmail/Asana tools, so this is either backend SMTP-on-create or a local scheduled routine that
  reads the table and sends. Pick a channel and it's a small add.
- **Bug button on text-only fields** (currently audio fields only; text-only fields use the comment box).
- **Email round-trip** as an alternative to the in-app thread, if reviewers prefer email.
