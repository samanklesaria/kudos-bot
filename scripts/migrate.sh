#!/usr/bin/env bash
set -euo pipefail

pg-schema-diff apply \
    --from-dsn "$DATABASE_URL" \
    --to-dir schema \
    --allow-hazards HAS_UNTRACKABLE_DEPENDENCIES,INDEX_BUILD,INDEX_DROPPED,DELETES_DATA \
    --skip-confirm-prompt \
    --disable-plan-validation
