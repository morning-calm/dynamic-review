#!/usr/bin/env bash
# Acquire the review-app baton on THIS host (Linux): refuse if another host is active,
# else pull the canonical review.db from R2, mark 'active', and start the systemd units.
# Pass --force to take over from a host that died un-released. Run with sudo if the units
# are system-wide. See docs/server-migration.md. (Windows equivalent: host_acquire.cmd)
set -e
cd "$(dirname "$0")/.."
python3 scripts/host_baton.py acquire "$@"
