# Final check + Publisher — test guide (simple version, 2026-08-22)

**Login:** `dave` / `dave-final-check`  (or `admin` with your usual password).
**Safety:** everything writes STAGING only — except the Publisher's red **Apply**
buttons (PRODUCTION). Stick to **Dry run** for this test. The two safe Applies:
*thumbnail local copy* and *overlay replace* (they only write local folders/R2).

## Start it

```powershell
# 1. fill the final-check lanes into the manifest (one-off per board change)
cd "D:\Dynamic Languages\Scripts"
py -3.12 Trello\export_review_trips.py --no-push

# 2. backend (publisher mode on, so you can test both sides at once)
cd D:\Projects\WebApp\review-app\backend
$env:REVIEW_APP_PUBLISHER = "1"
py -3.12 -m uvicorn --app-dir . app.main:app --host 127.0.0.1 --port 8000

# 3. frontend
cd D:\Projects\WebApp\review-app\frontend
npm run dev        # open http://127.0.0.1:5173, log in
```

## Test the Final check page (nav → **Final check**)

1. **List**: lane-10/10b/11 trips show; the audit section lists completed trips on
   no card ("Start final check" adds one by hand).
2. Open a trip → **7 checks**. Tick one, open a **sibling rung** of the same
   family — its family-level checks should already be green.
3. **1 Description**: read it; "Edit description…" jumps into the Descriptions flow.
4. **2 Categories**: add one → sibling-fit + enrichment panel pops; chips save.
5. **3 Title key**: edit → "Save to staging"; prod value alongside (needs a fresh
   snapshot).
6. **4 Location/pin**: change skybox (unknown name = amber warning), reorder tiles,
   **click the map** to place the pin → "Save pin".
   ⚠ Sanity-check one KNOWN pin (e.g. Kyoto on JapanMap) against the headset first.
7. **5 Static images**: play the scene audio, set appear/disappear ("= playhead"),
   Save (warnings are advisory); "Replace image…"; add a credit line at the bottom.
8. **6 Keywords**: 🎤 **Speak the answer** (your Azure key is live) → the heard
   forms appear; "+ add" one that failed.
9. **7 Thumbnail**: upload a JPEG → new image shows.
10. All 7 green → **Ready to publish** (header) queues the publish + Trello jobs.

## Test the Publisher page (nav → **Publisher**, rose link)

1. **Inbox**: your queued jobs are there → **Dry run** each and read the logs.
   (Apply = production — leave it.)
2. **Post-publish & tools**: Dry-run the buttons (bump / DocIDs / stage-9 just
   print the command they would run; the rest use the scripts' real dry-runs).
3. **Run gate report** (slow — minutes).

## Then

Note anything broken/odd — next session we fix, then: commit both repos, re-run
the export **with push**, laptop pull + `npm run build` + service restart.
(The laptop never sets `REVIEW_APP_PUBLISHER`.)
