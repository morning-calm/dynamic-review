# Delta reviews — re-confirming changed clips on already-approved trips

**Shipped 2026-07-23.** When the pipeline regenerates a handful of clips on a trip
that already passed its lane-7 review (first real case: the 2026-07-22/23
quiz-variety remediation across 12 completed A12 trips), the reviewer must re-confirm
**only the changed clips**, not the whole trip. This doc is the contract between the
Scripts repo (producer) and the review app (consumer).

## The manifest (produced by the Scripts repo)

One JSON object per remediated trip at **`review-audio/_delta/<contentId>.json`**
(same R2 bucket as the trip audio):

```jsonc
{
  "contentId": "Lake_District3_A12_EN",   // must equal the filename stem (else ignored, loud)
  "created": "2026-07-22",                // display only
  "reason": "quiz-variety remediation",   // display only
  "scenes": [
    {
      "index": 6,                          // 0-based, same as the mp3 stems / quickTrips[]
      "clips": ["6_q", "6_a1", "6_a2", "6_a3"],
      "questionKey": "What is the water doing?",          // display hints only — the app
      "questionOptionKeys": ["Falling down the rocks.", …] // always shows LIVE staging text
    }
  ]
}
```

- **Clip stems** mirror the app's mp3 naming: `6` = SceneDesc, `6_q` = questionKey,
  `6_a1`…`6_a3` = questionOption 1–3. `6_a` (the spoken ANSWER take) has no
  reviewable field and is skipped (loudly logged).
- The **new audio must already be at `review-audio/<contentId>/<stem>.mp3`** and the
  **new text already written to staging** before the manifest is uploaded — the app
  treats both as the source of truth and uses the manifest only to know WHICH
  clips changed.

## What the app does (`deltas.py` + `sessions.delta_cards`/`open_delta`)

1. **Discovery** — the trip list page also loads `GET /api/deltas`: every `_delta/`
   manifest whose trip is in `completed_trips`, language-ACL-filtered exactly like
   the trip itself. NOT manifest-of-lanes-driven: these trips left Trello 6/7 at
   their original approval. A trip **not** completed (open full review, or
   un-completed by an admin) never shows a card — **the full review supersedes the
   delta** with no extra logic.
2. **The delta card** ("N changed clips") opens `POST /api/deltas/{cid}/open` → a
   **delta session**: a completely normal session (same Review UI, same per-clip
   play / coverage / regenerate / splice / import / flag controls, same staging
   writes) that is seeded with **only the manifest's fields**. Text seeds from live
   staging; audio seeds **fresh from R2** (`_delta_seed_dir` — never the local
   trees or the `_r2_seed_cache`, both of which can still hold pre-remediation
   takes; fetched clips also refresh the seed cache in passing). The trip's
   completed status is **not** touched by opening.
3. **Full vs delta sessions never shadow each other**: `sessions.delta_json` marks a
   delta session, and the two resume paths filter on it (`IS NULL` / `IS NOT NULL`).
4. **Approve** (admin, after the reviewer submits) runs the identical commit path —
   changed text → staging, changed takes → master promotion + R2 mirror, approvals
   audit row, `completed_trips` upsert (bumping `completed_at`, which is exactly the
   **re-finalise signal** Stage 9 keys on via `completed_trips.json`), Trello
   comment (no card move) — and then **deletes `_delta/<contentId>.json`**.
   If nothing was edited (all clips simply confirmed), nothing is written — the new
   audio/text were already live — and the manifest is still consumed.

## How the Scripts side verifies consumption

**A delta is consumed ⇔ its `_delta/<contentId>.json` object is gone from R2.**
Poll with the same `Cloudfare_*` creds the uploader used:

```python
import boto3, os
s3 = boto3.client("s3", endpoint_url=os.environ["Cloudfare_S3_API_Endpoint"],
                  aws_access_key_id=os.environ["Cloudfare_Access_Key_ID"],
                  aws_secret_access_key=os.environ["Cloudfare_Secret_Access_Key"])
def delta_consumed(cid: str) -> bool:
    try:
        s3.head_object(Bucket="review-audio", Key=f"_delta/{cid}.json")
        return False          # still pending review
    except s3.exceptions.ClientError:
        return True           # gone = reviewed & approved
```

Corroborating signals after consumption:
- the trip's entry in `completed_trips.json` (local + `_bus/completed_trips.json` on
  R2) carries a **newer `completed_at`** than before → Stage 9 should re-finalise;
- if the reviewer corrected a clip, the corrected bytes are at
  `review-audio/<cid>/<stem>.mp3` (canonical key, as always).

If approve's delete ever fails (R2 hiccup), the app logs
`!! delta manifest … could NOT be deleted` and hides the card anyway (an approved
delta session newer than the manifest suppresses it) — delete the object manually so
the Scripts side agrees.

## Edge cases / operator notes

- **Un-complete while a delta is open**: the card disappears (trip is no longer
  completed) and the full review takes over. An in-flight delta session lingers
  invisibly (it still appears in the admin review queue if already `submitted` —
  approve or request-changes clears it). Re-completing the trip brings the card
  back and resumes that session.
- **Uploading a second remediation round** for the same trip: just upload a new
  manifest (it may re-list old scenes plus new ones). A newer `LastModified` than
  the last approved delta session makes the card show again.
- **A manifest clip missing from `review-audio/<cid>/`**: that field seeds
  text-only (audio tools disabled); if NONE of the clips resolve the open 422s
  (`delta_audio_missing`).
- **Live host**: like everything else, the running system is the Ubuntu laptop —
  deploy there (git pull + restart uvicorn; the tunnel must stay up) before
  expecting cards to appear for reviewers.
