#!/usr/bin/env bash
# Release the review-app baton on THIS host (Linux): stop the systemd units, back up
# review.db to R2, mark 'released' so another host may acquire. Run with sudo if the
# review-app/review-tunnel units are system-wide. See docs/server-migration.md.
# (Windows equivalent: host_release.cmd)
set -e
cd "$(dirname "$0")/.."
python3 scripts/host_baton.py release
