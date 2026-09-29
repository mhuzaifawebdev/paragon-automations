#!/usr/bin/env bash
# Enriches every institute marked "pending" in the Google Sheet, then publishes the results and marks the rows done.
# Safe to run from cron every few minutes: it exits at once if a run is already going or nothing is pending.
#   */5 * * * *  /opt/paragon/deploy/vps/run_pending.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
exec 9>/tmp/paragon-enrich.lock
flock -n 9 || { echo "$(date -Is) already running"; exit 0; }
python3 scraper2/run.py --pending --publish --workers "${WORKERS:-6}" >> data/enrich.log 2>&1
