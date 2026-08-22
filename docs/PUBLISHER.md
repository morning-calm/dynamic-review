# Running the Publisher (workstation console)

The review app runs in TWO places. Know which one your browser is talking to:

| Instance | URL | Who/where | Can it publish? |
|---|---|---|---|
| **Laptop (live)** | the usual tunnel URL | anyone, anywhere | View + **queue** only — Apply/tools return 403 |
| **Workstation (publisher)** | `http://127.0.0.1:8010` | you, at this desk | **Everything** — dry-run, Apply, tools, wizard, Claude handoff |

The workstation instance is the publisher because it is started with
`REVIEW_APP_PUBLISHER=1` **and** it is the only machine holding the production
Firebase key, Trello credentials, S3 creds and the local content trees. The env
var is the switch; the credentials are the wall.

## Start it (daily)

Double-click **`scripts\publisher.cmd`**.

That's all — it starts the app in publisher mode on `127.0.0.1:8010` (loopback
only, never tunnelled) and opens it in an Edge app window after ~4 s. Log in
with your workstation admin login. **Closing the black console window stops
it.**

If the Edge window doesn't appear, just browse to `http://127.0.0.1:8010`.

## One-time setup (already done on this workstation)

1. Build the frontend the console serves:
   `cd frontend && npm run build`
2. Create your admin login in the WORKSTATION's own database (it is separate
   from the laptop's — review state lives on the laptop; this instance only
   needs a login):
   `cd backend && py -3.12 manage.py add-user --username <name> --role admin`

## After app updates

The console serves the BUILT frontend, so after pulling review-app changes:

```
git pull
cd frontend && npm run build
```

then restart `publisher.cmd`. (Backend changes need only the restart.)

## Troubleshooting

- **"frontend\dist is missing"** → run the build (above).
- **Login rejected** → you're hitting the workstation DB; add the user (setup
  step 2). Laptop passwords don't carry over.
- **Port 8010 busy** → a previous console window is still open; close it.
- **Buttons say "not the publisher" / 403** → you're on the laptop URL, not
  `127.0.0.1:8010`. Queue there, execute here.
- **Dev testing instead** (hot reload, port 5173/8000): see
  `docs/final-check-dev-test.md` — that's a separate flow from this console.
