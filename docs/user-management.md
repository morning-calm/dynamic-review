# Managing review-app users & passwords (from the workstation, for the live laptop)

The live host is the **Ubuntu laptop** (`dynamic-languages-Lenovo-Z580`). All real
reviewer/admin accounts live in **its** `backend/review.db` — NOT the copy on this
workstation (the two databases are independent; changing a password here does nothing
for the live app).

User admin is done with `backend/manage.py`, run **on the laptop over SSH** from this
workstation (`ssh review-laptop` is a key-based alias in `~/.ssh/config`, no password
prompt). Always use the same interpreter the server runs with:

```
/home/dynamic-languages/Desktop/Server/Scripts/.venv/bin/python
```

## The command template

Run from any PowerShell/Git-Bash prompt on the workstation:

```bash
ssh review-laptop "cd ~/Desktop/Server/review-app/backend && \
  /home/dynamic-languages/Desktop/Server/Scripts/.venv/bin/python manage.py <subcommand> ..."
```

No service restart is needed for any user change — the backend reads `users` /
`auth_sessions` from the DB on every request.

## Common operations

**Change (reset) a user's password** — the case this doc exists for:

```bash
ssh review-laptop "cd ~/Desktop/Server/review-app/backend && \
  /home/dynamic-languages/Desktop/Server/Scripts/.venv/bin/python manage.py reset-password --username german"
```

- With no `--password`, a strong password is **generated and printed ONCE** — copy it
  from the terminal immediately; only the PBKDF2 hash is stored, it is never
  recoverable later.
- To set a specific password instead: append `--password 'TheNewPassword'` (quote it).
- Resetting **revokes every active login session** for that user (they are logged out
  everywhere and must sign in with the new password).

**List users** (who exists, role, active, language scope):

```bash
ssh review-laptop "cd ~/Desktop/Server/review-app/backend && \
  /home/dynamic-languages/Desktop/Server/Scripts/.venv/bin/python manage.py list-users"
```

**Create a reviewer** (language-scoped — they only see trips in their languages):

```bash
... manage.py add-user --username maria --role reviewer --languages Spanish
```

Valid languages: `English, Japanese, Mandarin, Spanish, French, German, Italian`
(must stay in step with `audio_core.language_of` — see the comment on
`VALID_LANGUAGES` in `manage.py`). Roles: `admin`, `reviewer` (admins bypass
language scoping).

**Other subcommands:** `set-languages`, `set-role`, `set-email` (used by the
activity notifier / findings emails), `deactivate` (disables login AND revokes
sessions — prefer this over deleting).

## Gotchas

- **`manage.py` version skew:** the laptop runs whatever is checked out at
  `~/Desktop/Server/review-app`. If a subcommand or language is missing there,
  `git -C ~/Desktop/Server/review-app pull` first (the fix/feature must be pushed to
  `morning-calm/dynamic-review` main).
- **Passwords travel in your terminal only.** Share them with the reviewer over a
  channel you trust; there is no email-invite flow.
- **Don't edit the DB by hand** — `manage.py` also handles hashing and session
  revocation; a raw SQL password change would leave old tokens valid.
- The workstation's own `backend/review.db` is a dev copy. If you ever run the app
  locally and want matching users, run the same `manage.py` commands locally
  (`py -3.12 manage.py ...` from `backend/`).
- Backups of the live DB (including password hashes) go to R2 per
  `docs/backup-and-restore.md`; that is also why `review.db` is deliberately not in git.
