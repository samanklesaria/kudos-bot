-- For each person, gather how much they gave, receieved, and are owed
CREATE VIEW giver_balances AS
SELECT *,
    GREATEST(0, LEAST(given, received) - redeemed)::INTEGER AS owed
FROM (SELECT
        giver_id,
        COUNT(*) FILTER (WHERE deleted_at IS NULL) AS given,
        (SELECT COUNT(*) FROM kudos r WHERE r.recipient_id = k.giver_id AND r.deleted_at IS NULL) AS received,
        COUNT(*) FILTER (WHERE redeemed_at IS NOT NULL AND deleted_at IS NULL) AS redeemed
    FROM kudos k
    GROUP BY giver_id) giver_stats;

-- Unredeemed rows eligible for redemption across all givers, in FIFO order.
-- For each giver, picks their oldest unredeemed rows up to their owed count.
CREATE VIEW pending_redemptions AS
SELECT k.id AS kudos_id, k.giver_id
FROM (
    SELECT id, giver_id, created_at,
           ROW_NUMBER() OVER (PARTITION BY giver_id ORDER BY created_at) AS rn
    FROM kudos
    WHERE redeemed_at IS NULL AND deleted_at IS NULL) k
JOIN giver_balances go ON go.giver_id = k.giver_id
WHERE k.rn <= go.owed
ORDER BY k.created_at;

-- Returns the applicable budget for a given month (or the most recent prior one).
CREATE FUNCTION effective_budget()
RETURNS TABLE(conversion_rate NUMERIC, point_budget INTEGER) AS $fn$
BEGIN
    RETURN QUERY
    SELECT b.conversion_rate, b.point_budget
    FROM budgets b
    WHERE b.month_date <= CURRENT_DATE
    ORDER BY b.month_date DESC
    LIMIT 1;
END;
$fn$ LANGUAGE plpgsql STABLE;

-- Last month's redeemed kudos aggregated by recipient.
CREATE VIEW last_month_redemptions AS
SELECT k.recipient_id,
       array_agg(k.channel_id) AS channels,
       array_agg(k.message_ts) AS timestamps,
       sum(b.conversion_rate) AS total
FROM kudos k
JOIN LATERAL (
    SELECT conversion_rate FROM budgets
    WHERE month_date <= date_trunc('month', k.redeemed_at)::date
    ORDER BY month_date DESC LIMIT 1) b ON TRUE
WHERE k.redeemed_at >= date_trunc('month', CURRENT_DATE) - interval '1 month'
  AND k.redeemed_at < date_trunc('month', CURRENT_DATE)
  AND k.deleted_at IS NULL
GROUP BY k.recipient_id;

-- How many points have been redeemed in a given month.
CREATE FUNCTION redeemed_this_month(p_month DATE DEFAULT date_trunc('month', CURRENT_DATE)::date)
RETURNS INTEGER AS $fn$
BEGIN RETURN (
        SELECT COUNT(*)::int FROM kudos
        WHERE redeemed_at IS NOT NULL AND deleted_at IS NULL
          AND redeemed_at >= p_month
          AND redeemed_at < p_month + INTERVAL '1 month');
END;
$fn$ LANGUAGE plpgsql STABLE;

-- Validates that a giver can give kudos right now. Returns NULL on success, error message on failure.
CREATE FUNCTION check_kudos_limits(p_giver_id TEXT, p_recipient_id TEXT)
RETURNS TEXT AS $fn$
BEGIN
    IF p_giver_id = p_recipient_id THEN
        RETURN $$You can't give kudos to yourself!$$;
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext(p_giver_id));

    IF EXISTS(SELECT 1 FROM kudos k
              WHERE k.giver_id = p_giver_id AND k.deleted_at IS NULL
                AND k.created_at >= date_trunc('day', NOW())) THEN
        RETURN $$You've already given kudos today. Try again tomorrow!$$;
    END IF;

    IF EXISTS(SELECT 1 FROM kudos k
              WHERE k.giver_id = p_giver_id AND k.recipient_id = p_recipient_id
                AND k.deleted_at IS NULL
                AND k.created_at >= date_trunc('month', NOW())) THEN
        RETURN $$You've already given kudos to this person this month.$$;
    END IF;

    RETURN NULL;
END;
$fn$ LANGUAGE plpgsql;

-- Attempts to redeem a point for the giver.
-- Redemption succeeds only when all three conditions hold:
--   1. The giver is owed points (min(given, received) > redeemed)
--   2. No other givers have pending redemptions (these must be handled by process_overflow.py first)
--   3. This month's budget has capacity
-- Notification flags are derived from the outcome:
--   - notify_budget_exhausted: redemption succeeded AND it consumed the last budget slot
--   - notify_queued: redemption failed due to (2) or (3), AND this is the giver's
--     first pending item (owed = 1), so they haven't been notified yet
CREATE FUNCTION try_redeem(p_kudos_id INTEGER, p_giver_id TEXT)
RETURNS TABLE(redeemed BOOLEAN, notify_budget_exhausted BOOLEAN, notify_queued BOOLEAN) AS $fn$
BEGIN
    -- Serialize all budget operations so concurrent redemptions can't overspend
    PERFORM pg_advisory_xact_lock(0);

    RETURN QUERY
    WITH ctx AS (
        SELECT
            COALESCE((SELECT owed FROM giver_balances WHERE giver_id = p_giver_id), 0) AS owed,
            eb.point_budget AS budget,
            redeemed_this_month() AS used_this_month,
            EXISTS(SELECT 1 FROM pending_redemptions WHERE kudos_id <> p_kudos_id) AS has_pending
        FROM effective_budget() eb
    ),
    do_redeem AS (
        UPDATE kudos SET redeemed_at = NOW()
        WHERE id = p_kudos_id
          AND EXISTS(SELECT 1 FROM ctx
                     WHERE owed > 0 AND NOT has_pending AND used_this_month < budget)
        RETURNING TRUE AS ok)
    SELECT
        EXISTS(SELECT 1 FROM do_redeem),
        EXISTS(SELECT 1 FROM do_redeem) AND (SELECT used_this_month + 1 >= budget FROM ctx),
        NOT EXISTS(SELECT 1 FROM do_redeem) AND (SELECT owed > 0 FROM ctx)
            AND (SELECT owed FROM ctx) = 1;
END;
$fn$ LANGUAGE plpgsql;

-- Main entry point: validate, insert, and attempt redemption.
CREATE FUNCTION give_kudos(
    p_giver_id TEXT,
    p_recipient_id TEXT,
    p_channel_id TEXT,
    p_message_ts TEXT,
    p_message_text TEXT DEFAULT NULL
) RETURNS TABLE(error TEXT, redeemed_amount NUMERIC,
                notify_budget_exhausted BOOLEAN, notify_queued BOOLEAN) AS $fn$
DECLARE
    v_error TEXT;
    v_kudos_id INTEGER;
BEGIN
    v_error := check_kudos_limits(p_giver_id, p_recipient_id);
    IF v_error IS NOT NULL THEN
        RETURN QUERY SELECT v_error, 0::NUMERIC, FALSE, FALSE;
        RETURN;
    END IF;

    INSERT INTO kudos (giver_id, recipient_id, channel_id, message_ts, message_text)
    VALUES (p_giver_id, p_recipient_id, p_channel_id, p_message_ts, p_message_text)
    ON CONFLICT (channel_id, message_ts) WHERE deleted_at IS NULL DO NOTHING
    RETURNING id INTO v_kudos_id;

    IF v_kudos_id IS NULL THEN
        RETURN;
    END IF;

    RETURN QUERY SELECT NULL::TEXT,
           COALESCE(CASE WHEN r.redeemed THEN (SELECT conversion_rate FROM effective_budget()) END, 0::NUMERIC),
           COALESCE(r.notify_budget_exhausted, FALSE), COALESCE(r.notify_queued, FALSE)
    FROM try_redeem(v_kudos_id, p_giver_id) r;

    -- If try_redeem returned no rows (e.g. no budget configured), still confirm the kudos
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::TEXT, 0::NUMERIC, FALSE, FALSE;
    END IF;
END;
$fn$ LANGUAGE plpgsql;

-- Weekly acquired vs redeemed with effective budget for each week.
CREATE VIEW weekly_kudos AS
SELECT yw, ym, acquired, redeemed, b.point_budget, b.conversion_rate FROM (
    SELECT to_char(k.created_at, 'IYYY-IW') AS yw,
           to_char(k.created_at, 'YYYY-MM') AS ym,
           COUNT(*)::int AS acquired,
           COUNT(*) FILTER (WHERE k.redeemed_at IS NOT NULL)::int AS redeemed
    FROM kudos k WHERE k.deleted_at IS NULL
    GROUP BY yw, ym) w
JOIN LATERAL (
    SELECT point_budget, conversion_rate FROM budgets
    WHERE month_date <= (w.ym || '-01')::date
    ORDER BY month_date DESC LIMIT 1) b ON TRUE
ORDER BY yw;

-- Points received per person.
CREATE VIEW leaderboard AS
SELECT u.display_name, COUNT(*)::int AS received
FROM kudos k JOIN users u ON u.id = k.recipient_id
WHERE k.deleted_at IS NULL
GROUP BY u.id, u.display_name ORDER BY received DESC LIMIT 50;

-- Topic cluster fractions per month (only clusters >= 10%).
CREATE VIEW topic_stream AS
SELECT month, cluster_id, summary, frac FROM (
    SELECT to_char(k.created_at, 'YYYY-MM') AS month,
           c.id AS cluster_id, c.summary,
           COUNT(*)::float / SUM(COUNT(*)) OVER (PARTITION BY to_char(k.created_at, 'YYYY-MM')) AS frac
    FROM clusters c
    JOIN cluster_members cm ON cm.cluster_id = c.id
    JOIN kudos k ON k.id = cm.kudos_id
    WHERE k.deleted_at IS NULL
    GROUP BY month, c.id, c.summary) t
WHERE frac >= 0.1
ORDER BY month, summary;

-- All kudos with giver/recipient display names, for drill-down tables.
CREATE VIEW kudos_messages AS
SELECT k.id, k.giver_id, k.recipient_id,
       ug.display_name AS giver, ur.display_name AS recipient,
       k.message_text AS message, k.created_at::date AS date,
       to_char(k.created_at, 'YYYY-MM') AS month
FROM kudos k
JOIN users ug ON ug.id = k.giver_id
JOIN users ur ON ur.id = k.recipient_id
WHERE k.deleted_at IS NULL;

-- Soft-delete a kudos. Returns one row per deleted kudos, or no rows if not found.
CREATE FUNCTION delete_kudos(p_channel_id TEXT, p_message_ts TEXT)
RETURNS TABLE(was_redeemed BOOLEAN, recipient TEXT) AS $fn$
BEGIN
    RETURN QUERY
    UPDATE kudos SET deleted_at = NOW()
    WHERE channel_id = p_channel_id AND message_ts = p_message_ts AND deleted_at IS NULL
    RETURNING redeemed_at IS NOT NULL AS was_redeemed, recipient_id;
END;
$fn$ LANGUAGE plpgsql;