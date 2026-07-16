-- How many points-worth of money is each user owed?
CREATE VIEW balances AS
SELECT *, GREATEST(0, LEAST(given, received))::INTEGER AS owed
FROM (SELECT giver_id,
        COUNT(*) FILTER (WHERE deleted_at IS NULL AND NOT giver_overflow and redeemed_at is NULL) AS given,
        (SELECT COUNT(*) FROM kudos r WHERE r.recipient_id = k.giver_id AND r.deleted_at IS NULL AND NOT r.recipient_overflow) AS received
    FROM kudos k
    GROUP BY giver_id) sub;

-- Applicable budget for the most recent month
-- TODO: union all with a default row of (0,0)
CREATE VIEW effective_budget AS
SELECT b.conversion_rate, b.point_budget
    FROM budgets b
    WHERE b.month_date <= CURRENT_DATE
    ORDER BY b.month_date DESC
    LIMIT 1;

-- Last month's redeemed kudos aggregated by recipient.
CREATE VIEW last_month_redemptions AS
SELECT k.recipient_id,
       array_agg(k.channel_id) AS channels,
       array_agg(k.message_ts) AS timestamps,
       count(*) * b.conversion_rate AS total
FROM kudos k, effective_budget b
WHERE k.redeemed_at >= date_trunc('month', CURRENT_DATE) - interval '1 month'
  AND k.redeemed_at < date_trunc('month', CURRENT_DATE)
  AND k.deleted_at IS NULL
  AND NOT k.recipient_overflow
GROUP BY k.recipient_id, b.conversion_rate;

-- How many points have been redeemed in a given month.
CREATE FUNCTION redeemed_this_month(p_month DATE DEFAULT date_trunc('month', CURRENT_DATE)::date)
RETURNS INTEGER LANGUAGE SQL STABLE AS $$
    SELECT COUNT(*)::int FROM kudos
    WHERE deleted_at IS NULL
      AND redeemed_at >= p_month
      AND redeemed_at < p_month + INTERVAL '1 month';
$$;

-- Validates that a giver can give kudos right now. Returns NULL on success, error message on failure.
CREATE FUNCTION check_kudos_limits(p_giver_id TEXT, p_recipient_id TEXT)
RETURNS TEXT AS $fn$
BEGIN
    IF p_giver_id = p_recipient_id THEN
        RETURN $$You can't give kudos to yourself!$$;
    END IF;

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

CREATE VIEW to_redeem AS
SELECT giver_id,
    (SELECT k.id FROM kudos k
        WHERE recipient_id = b.giver_id
        AND redeemed_at IS NULL AND deleted_at IS NULL AND NOT recipient_overflow
        ORDER BY created_at LIMIT 1) as kudos_id
    FROM balances b WHERE owed > 0;

-- How many points remain in this month's budget.
-- ponytail: plpgsql to defer validation — effective_budget view may not exist yet at creation time
CREATE FUNCTION remaining_budget()
RETURNS INTEGER AS $fn$
BEGIN RETURN (
    SELECT GREATEST(0, b.point_budget - redeemed_this_month())::int
    FROM effective_budget b);
END;
$fn$ LANGUAGE plpgsql STABLE;

-- Attempts to redeem points for both giver and recipient.
CREATE FUNCTION try_redeem(p_giver_id TEXT, p_recipient_id TEXT)
RETURNS TABLE(redeemed_user_ids TEXT[], notify_budget_exhausted BOOLEAN) AS $fn$
DECLARE v_remaining INTEGER := COALESCE(remaining_budget(), 0);
BEGIN
    RETURN QUERY
    WITH updated AS (
        UPDATE kudos SET redeemed_at = NOW()
        WHERE id IN (SELECT kudos_id FROM to_redeem LIMIT v_remaining)
        RETURNING id, recipient_id)
    SELECT
        COALESCE(array_agg(DISTINCT recipient_id::TEXT), '{}'),
        count(*) = v_remaining AND v_remaining > 0
    FROM updated;
END;
$fn$ LANGUAGE plpgsql;

-- Main entry point: validate, insert, and attempt redemption.
CREATE FUNCTION give_kudos(
    p_giver_id TEXT,
    p_recipient_id TEXT,
    p_channel_id TEXT,
    p_message_ts TEXT,
    p_message_text TEXT DEFAULT NULL
) RETURNS TABLE(error TEXT, conversion_rate NUMERIC, redeemed_user_ids TEXT[],
                notify_budget_exhausted BOOLEAN) AS $fn$
DECLARE
    v_error TEXT;
    v_inserted BOOLEAN;
BEGIN
    v_error := check_kudos_limits(p_giver_id, p_recipient_id);
    WITH ins AS (
        INSERT INTO kudos (giver_id, recipient_id, channel_id, message_ts, message_text)
        SELECT p_giver_id, p_recipient_id, p_channel_id, p_message_ts, p_message_text
        WHERE v_error IS NULL
        ON CONFLICT (channel_id, message_ts) WHERE deleted_at IS NULL DO NOTHING
        RETURNING id)
    SELECT EXISTS(SELECT 1 FROM ins) INTO v_inserted;
    IF NOT v_inserted AND v_error IS NULL THEN RETURN; END IF;
    RETURN QUERY
    SELECT v_error,
        b.conversion_rate,
        r.redeemed_user_ids,
        r.notify_budget_exhausted
    FROM try_redeem(p_giver_id, p_recipient_id) r
    LEFT JOIN effective_budget b ON TRUE;
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
GROUP BY u.id, u.display_name ORDER BY received DESC LIMIT 25;

-- Topic cluster fractions per month (only clusters >= 10%).
CREATE VIEW topic_stream AS
SELECT month, cluster_id, summary, frac FROM (
    SELECT to_char(k.created_at, 'YYYY-MM') AS month,
           c.id AS cluster_id, c.summary,
           COUNT(*)::float / SUM(COUNT(*)) OVER (PARTITION BY to_char(k.created_at, 'YYYY-MM')) AS frac
    FROM kudos k
    JOIN cluster_members cm ON cm.kudos_id = k.id
    JOIN clusters c ON c.id = cm.cluster_id
    GROUP BY month, c.id, c.summary) t
WHERE frac >= 0.1
ORDER BY month, summary;

-- Human-readable kudos for dashboard drill-downs.
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
RETURNS TABLE(was_redeemed BOOLEAN, recipient TEXT) LANGUAGE SQL AS $$
UPDATE kudos SET deleted_at = NOW()
WHERE channel_id = p_channel_id AND message_ts = p_message_ts AND deleted_at IS NULL
RETURNING redeemed_at IS NOT NULL AS was_redeemed, recipient_id;
$$;
