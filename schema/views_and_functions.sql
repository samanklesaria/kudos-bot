-- How many points-worth of money is each user owed?
CREATE VIEW balances AS
SELECT *, GREATEST(0, LEAST(given, received) - redeemed)::INTEGER AS owed
FROM (SELECT giver_id,
        COUNT(*) FILTER (WHERE NOT giver_overflow) AS given,
        (SELECT COUNT(*) FROM kudos r WHERE r.recipient_id = k.giver_id AND NOT r.recipient_overflow) AS received,
        (SELECT COUNT(*) FROM kudos r WHERE r.recipient_id = k.giver_id AND NOT r.recipient_overflow AND r.redeemed_at IS NOT NULL) AS redeemed
    FROM kudos k
    GROUP BY giver_id) sub;

-- Applicable budget as of a given date (defaults to today).
CREATE FUNCTION effective_budget(p_as_of DATE DEFAULT CURRENT_DATE)
RETURNS TABLE(conversion_rate NUMERIC, point_budget INTEGER) LANGUAGE SQL STABLE AS $$
    (SELECT b.conversion_rate, b.point_budget
    FROM budgets b
    WHERE b.month_date <= p_as_of
    ORDER BY b.month_date DESC)
    UNION ALL
    (SELECT 1, 0)
    LIMIT 1;
$$;

-- Last month's redeemed kudos aggregated by recipient.
CREATE VIEW last_month_redemptions AS
SELECT k.recipient_id,
       array_agg(k.channel_id) AS channels,
       array_agg(k.message_ts) AS timestamps,
       count(*) * b.conversion_rate AS total
FROM kudos k, effective_budget((date_trunc('month', CURRENT_DATE) - interval '1 month')::date) b
WHERE k.redeemed_at >= date_trunc('month', CURRENT_DATE) - interval '1 month'
  AND k.redeemed_at < date_trunc('month', CURRENT_DATE)
  AND NOT k.recipient_overflow
GROUP BY k.recipient_id, b.conversion_rate;

-- How many points have been redeemed in a given month.
-- ponytail: plpgsql to defer validation — kudos table may not exist yet at creation time
CREATE FUNCTION redeemed_this_month(p_month DATE DEFAULT date_trunc('month', CURRENT_DATE)::date)
RETURNS INTEGER AS $fn$
BEGIN RETURN (
    SELECT COUNT(*)::int FROM kudos
    WHERE redeemed_at >= p_month
      AND redeemed_at < p_month + INTERVAL '1 month');
END;
$fn$ LANGUAGE plpgsql STABLE;

-- Validates that a giver can give kudos right now. Returns NULL on success, error message on failure.
CREATE FUNCTION check_kudos_limits(p_giver_id VARCHAR, p_recipient_id VARCHAR)
RETURNS VARCHAR LANGUAGE SQL AS $fn$
    SELECT CASE
        WHEN p_giver_id = p_recipient_id
            THEN 'You can''t give kudos to yourself!'
        WHEN bool_or(created_at >= date_trunc('day', NOW()))
            THEN 'You''ve already given kudos today. Try again tomorrow!'
        WHEN bool_or(recipient_id = p_recipient_id)
            THEN 'You''ve already given kudos to this person this month.'
    END
    FROM kudos
    WHERE giver_id = p_giver_id
        AND created_at >= date_trunc('month', NOW());
$fn$;

-- Pairs of (received kudos to redeem, giving kudos that redeems it).
CREATE VIEW to_redeem AS
SELECT kudos_id,
    (SELECT k.id FROM kudos k
        WHERE k.giver_id = sub.giver_id
        AND k.redeems IS NULL AND NOT k.giver_overflow
        ORDER BY k.created_at LIMIT 1) AS redeemer_id
FROM (SELECT giver_id,
    (SELECT k.id FROM kudos k
        WHERE k.recipient_id = b.giver_id
        AND k.redeemed_at IS NULL AND NOT k.recipient_overflow
        ORDER BY k.created_at LIMIT 1) AS kudos_id
    FROM balances b WHERE owed > 0) sub
WHERE kudos_id IS NOT NULL;

-- How many points remain in this month's budget.
CREATE FUNCTION remaining_budget(p_as_of DATE DEFAULT CURRENT_DATE)
RETURNS INTEGER LANGUAGE SQL STABLE AS $fn$
    SELECT GREATEST(0, b.point_budget - redeemed_this_month(p_as_of))::int
    FROM effective_budget(p_as_of) b;
$fn$;

-- Attempts to redeem points. p_as_of overrides the redemption timestamp (for simulation).
CREATE FUNCTION try_redeem(p_as_of TIMESTAMPTZ DEFAULT NOW())
RETURNS TABLE(redeemed_user_ids VARCHAR[], notify_budget_exhausted BOOLEAN) AS $fn$
DECLARE v_remaining INTEGER;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('try_redeem'));
    v_remaining := remaining_budget(p_as_of::date);
    RETURN QUERY
    WITH pairs AS (
        SELECT kudos_id, redeemer_id FROM to_redeem LIMIT v_remaining
    ),
    redeemed AS (
        UPDATE kudos SET redeemed_at = p_as_of
        WHERE id IN (SELECT kudos_id FROM pairs)
        RETURNING id, recipient_id
    ),
    linked AS (
        UPDATE kudos SET redeems = p.kudos_id
        FROM pairs p WHERE kudos.id = p.redeemer_id
    )
    SELECT
        COALESCE(array_agg(DISTINCT recipient_id), '{}'),
        count(*) = v_remaining AND v_remaining > 0
    FROM redeemed;
END;
$fn$ LANGUAGE plpgsql;

-- Main entry point: validate, insert, and attempt redemption.
CREATE FUNCTION give_kudos(
    p_giver_id VARCHAR,
    p_recipient_id VARCHAR,
    p_channel_id VARCHAR,
    p_message_ts VARCHAR,
    p_message_text TEXT DEFAULT NULL
) RETURNS TABLE(error VARCHAR, conversion_rate NUMERIC, redeemed_user_ids VARCHAR[],
                notify_budget_exhausted BOOLEAN) AS $fn$
DECLARE v_error VARCHAR(128);
BEGIN
    v_error := check_kudos_limits(p_giver_id, p_recipient_id);
    IF v_error IS NOT NULL THEN
        RETURN QUERY SELECT v_error, NULL::NUMERIC, '{}'::VARCHAR[], FALSE;
        RETURN;
    END IF;
    INSERT INTO kudos (giver_id, recipient_id, channel_id, message_ts, message_text)
    VALUES (p_giver_id, p_recipient_id, p_channel_id, p_message_ts, p_message_text)
    ON CONFLICT (channel_id, message_ts) DO NOTHING;
    IF NOT FOUND THEN RETURN; END IF;
    RETURN QUERY
    SELECT NULL::VARCHAR,
        b.conversion_rate,
        r.redeemed_user_ids,
        r.notify_budget_exhausted
    FROM try_redeem() r, effective_budget() b;
END;
$fn$ LANGUAGE plpgsql;

-- Weekly acquired vs redeemed with effective budget for each week.
CREATE VIEW weekly_kudos AS
SELECT yw, ym, acquired, redeemed, b.point_budget, b.conversion_rate FROM (
    SELECT to_char(k.created_at, 'IYYY-IW') AS yw,
           to_char(k.created_at, 'YYYY-MM') AS ym,
           COUNT(*)::int AS acquired,
           COUNT(*) FILTER (WHERE k.redeemed_at IS NOT NULL)::int AS redeemed
    FROM kudos k
    GROUP BY yw, ym) w
JOIN LATERAL effective_budget((w.ym || '-01')::date) b ON TRUE
ORDER BY yw;

-- Points received per person.
CREATE VIEW leaderboard AS
SELECT u.display_name, COUNT(*)::int AS received
FROM kudos k JOIN users u ON u.id = k.recipient_id
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
JOIN users ur ON ur.id = k.recipient_id;

-- Hard-delete a kudos, un-redeeming any kudos it had redeemed.
-- ponytail: plpgsql to defer validation — redeems column may not exist yet at creation time
CREATE PROCEDURE delete_kudos(p_channel_id VARCHAR, p_message_ts VARCHAR) LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE kudos SET redeemed_at = NULL, redeems = NULL
    WHERE id IN (SELECT redeems FROM kudos WHERE channel_id = p_channel_id AND message_ts = p_message_ts AND redeems IS NOT NULL);
    DELETE FROM kudos WHERE channel_id = p_channel_id AND message_ts = p_message_ts;
    PERFORM try_redeem();
END;
$fn$;
