# CEFR partial-audio refresh handoff — 2026-07-26

## Why this refresh exists

The corrected CEFR Phase D pass changed narration in seven staging-only trips.
The producer regenerated the affected audio through the standard Gemini and
ElevenLabs pipeline and uploaded the new MP3 masters to R2 `review-audio`.

`Jerez_CascoAntiguo_B1_ES` first required a **full 10-scene initial
generation** because preflight found no local master directory or R2 objects.
The producer then rejected the too-light two-scene text edit and rewrote all ten
narration scenes to B1 Mid. As a result, the complete Sara 0.85× set was
regenerated and uploaded again. The final staging script measures B1 Mid, 2.653,
up from B1 Low, 2.244.

No production, S3 or subtitle write was made.

## New R2 audio

| Trip | Files replaced or added |
|---|---|
| `Strasbourg3_A12_FR` | narration scenes 3, 4, 8, 9, 10, 12 |
| `Strasbourg5_A12_FR` | narration scenes 1, 3, 7, 9, 12, 13 |
| `Girona_A12_ES` | narration scenes 16, 20, 21 |
| `Florence3_A12_IT` | narration scenes 8, 9, 13, 14 |
| `Abbotsford_B1_EN` | narration scenes 1, 8, 12, 16, 22, 23 |
| `Melrose_B1_EN` | narration scenes 3, 8, 11, 12, 15, 16 |
| `Jerez_CascoAntiguo_B1_ES` | full replacement narration set, scenes 0–9, Sara 0.85× |

Verification completed on the producer:

- all ten final Jerez MP3s match their R2 ETags byte-for-byte;
- the other 31 local MP3 sizes still match their R2 objects;
- all 31 surgical MP3/OGG pairs exist locally;
- the 19 A12 clips carry the required 3-second trailing pad;
- no generation temp files remain;
- no reviewer-corrected `originals/` marker was overwritten.

## Queue and manifest

The legacy `Jerez_Trip` Trello card now contains:

```text
[review]
Jerez_CascoAntiguo_A12_ES lane=6
Jerez_CascoAntiguo_B1_ES  lane=6 voice=Sara gender=female
[/review]
```

The producer also carries the canonical strict-root family mapping:
`Research and Writing/data/Jerez_CascoAntiguo_B1_ES/source.json` with
`{"source_en_id": "Jerez_Trip"}`. This is load-bearing: without it the exporter
mistakes the legacy B1 rung for its own native family and forces it from
translator lane 6 to KP lane 7.

Run the normal exporter from the Scripts checkout to regenerate, commit and push
`trips_to_review.json`:

```powershell
py -3.12 Trello/export_review_trips.py
```

Expected manifest check:

- `Jerez_CascoAntiguo_B1_ES` is present once;
- lane is `6`;
- voice is `Sara`, gender is `female`;
- its card URL is the `Jerez_Trip` family card.

The all-scene follow-up does not change any manifest field, so commit `503da9a`
remains the correct manifest. No second manifest export is required solely for
this audio replacement.

## Live laptop refresh

The live app is on the Ubuntu laptop. Pull the manifest there, then use the
guarded refresh tool; do not hand-delete cache or sessions.

```bash
ssh review-laptop
cd ~/Desktop/Server/review-app
git pull

cat > /tmp/cefr-partial-audio-cids.txt <<'EOF'
Strasbourg3_A12_FR
Strasbourg5_A12_FR
Girona_A12_ES
Florence3_A12_IT
Abbotsford_B1_EN
Melrose_B1_EN
Jerez_CascoAntiguo_B1_ES
EOF

source ~/Desktop/Server/Scripts/.venv/bin/activate
python scripts/refresh_trips.py audit --file /tmp/cefr-partial-audio-cids.txt
```

If the earlier seven-trip refresh has already been completed, repeat the guarded
procedure for Jerez alone because all ten R2 objects have changed again:

```bash
printf '%s\n' Jerez_CascoAntiguo_B1_ES > /tmp/jerez-b1-refresh.txt
python scripts/refresh_trips.py audit --file /tmp/jerez-b1-refresh.txt
```

Follow each audit verdict:

- `CLEAR`: run guarded `clear` for that trip;
- `RESEED`: only run `reseed` if the tool reconfirms no reviewer work or recent
  presence;
- `HANDS OFF`: do not mutate it; coordinate with the reviewer.

After the list endpoint has refilled eligible caches:

```bash
python scripts/refresh_trips.py verify --file /tmp/cefr-partial-audio-cids.txt
```

For the Jerez-only repeat, use `/tmp/jerez-b1-refresh.txt` in the verification
command.

Success is cached bytes matching R2, allowing for any reviewer correction with
an `originals/<name>` marker. Jerez now requires a complete B1 script/audio
review. Abbotsford and Melrose remain at the human-review boundary; subtitles
and Stage 9/S3 work wait until that renewed review passes.
