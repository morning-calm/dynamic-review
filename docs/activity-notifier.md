# Activity notifier — email digest of reviewer/admin work

Emails **dave@dynamiclanguages.org** a short, rate-limited digest of who is working in the
review app: trips **started**, **finished**, and **90-min+ breaks** in between. Individual
field autosaves are "minor" and never emailed.

- **Script:** `scripts/activity_notifier.py` (stdlib only — no extra deps).
- **Wrapper:** `scripts/run_activity_notifier.cmd` → logs to `backend/notifier.log`.
- **Schedule:** Windows Task Scheduler task **`ReviewAppActivityNotify`**, every **15 min**
  (mirrors `ReviewAppDbBackup`). 15 min is the poll grain; the rate limits decide sends.
- **State:** `backend/notifier_state.json` (gitignored). **Config/secrets:**
  `scripts/notifier_config.json` (gitignored).

## Safety
Opens `review.db` **read-only** and only writes its own state/log — it never touches the
running app or the DB writer, so it is safe to run while a review is live.

## What it reports (attribution)
- **Finish** (submitted / approved / completed): **exact** — from `submitted_by` /
  `approved_by` / `completed_by`.
- **Start / break:** best-effort — `field_edits` has no `user_id`, so it attributes to the
  language specialist (Mandarin→ted, Japanese→toshifumi) if they logged in within 12 h of the
  activity, else the language-capable user with the most recent prior login, else the language
  default. Good enough for a "who's working" digest; exact attribution would need a one-line
  backend change to stamp `user_id` on edits.

## Rate limits (hard)
`<= 1 email/hour`, `<= 10 emails/day`. Events accumulate in a pending buffer and ride out
**batched** with the next allowed email — nothing is lost, only delayed. A quiet day sends
zero.

## Email transport — MailWizz v2 (on SES)
Copy `scripts/notifier_config.example.json` → `scripts/notifier_config.json` and fill:
`base_url` (MailWizz API root, e.g. `…/api/index.php`), `X-Api-Key` (the v2 API key),
`from_email`/`from_name`, `to_email`/`to_name`. The script POSTs one email to the MailWizz
`transactional-emails` endpoint as form-data (fields under `email[...]`, `body`/`plain_text`
base64-encoded, `send_at` in UTC), authenticated by the single `X-Api-Key` header — no HMAC.
Verify delivery any time with `py -3.12 scripts/activity_notifier.py --test` (expects
`HTTP 201 {"status":"success",...}`).

## Operate / troubleshoot
```bash
py -3.12 scripts/activity_notifier.py --dry-run   # detect + print the email, send nothing
py -3.12 scripts/activity_notifier.py --force     # bypass the 1/hour gate (still <=10/day)
py -3.12 scripts/activity_notifier.py --reset-baseline  # re-seed baseline, emit nothing
```
- **First run ever** (or `--reset-baseline`) seeds a **silent baseline** of current sessions
  so history is never blasted; only activity *after* that generates events.
- If `notifier_config.json` is missing, runs still detect and **hold** events until it exists.
- Send failures never crash the task — events are held for the next run.

## Re-register the task (new machine / after deletion)
```powershell
$cmd = "D:\Projects\WebApp\review-app\scripts\run_activity_notifier.cmd"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$cmd`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(2)
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
  -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition
$principal = New-ScheduledTaskPrincipal -UserId "david" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "ReviewAppActivityNotify" -Action $action -Trigger $trigger `
  -Principal $principal -Force
```
