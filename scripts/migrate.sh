#!/usr/bin/env bash
set -euo pipefail

# Drop all views and functions so pg-schema-diff doesn't hit dependency issues.
psql "$DATABASE_URL" <<'SQL'
DO $$ DECLARE r RECORD;
BEGIN
    FOR r IN SELECT viewname FROM pg_views WHERE schemaname = 'public' LOOP
        EXECUTE 'DROP VIEW IF EXISTS ' || quote_ident(r.viewname) || ' CASCADE';
    END LOOP;
    FOR r IN SELECT oid::regprocedure::text AS sig FROM pg_proc
             WHERE pronamespace = 'public'::regnamespace
               AND prokind IN ('f', 'p')
               AND NOT EXISTS (SELECT 1 FROM pg_depend d
                   WHERE d.objid = oid AND d.deptype = 'e') LOOP
        EXECUTE 'DROP ROUTINE IF EXISTS ' || r.sig || ' CASCADE';
    END LOOP;
END $$;
SQL

pg-schema-diff apply \
    --from-dsn "$DATABASE_URL" \
    --to-dir schema \
    --allow-hazards HAS_UNTRACKABLE_DEPENDENCIES,INDEX_BUILD,INDEX_DROPPED,DELETES_DATA \
    --skip-confirm-prompt \
    --disable-plan-validation
