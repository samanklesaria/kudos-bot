import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"


@pytest.fixture
def conn():
    url = os.environ.get("KUDOS_TEST_DATABASE_URL", "postgresql://localhost/kudos_test")
    c = psycopg.connect(url)
    c.execute("DROP SCHEMA IF EXISTS public CASCADE")
    c.execute("CREATE SCHEMA public")
    for sql_file in sorted(SCHEMA_DIR.glob("*.sql")):
        c.execute(sql_file.read_text())
    c.commit()
    yield c
    c.close()


def _set_budget(conn, points=100, rate=5.0):
    conn.execute(
        "INSERT INTO budgets (month_date, point_budget, conversion_rate) "
        "VALUES (date_trunc('month', CURRENT_DATE)::date, %s, %s) "
        "ON CONFLICT (month_date) DO UPDATE SET point_budget = %s, conversion_rate = %s",
        (points, rate, points, rate))


class GiveResult:
    """Wrapper for give_kudos return row. None row means silent no-op (duplicate message_ts)."""
    def __init__(self, row):
        if row is None:
            self.error, self.amount, self.notify_budget, self.notify_queued = None, 0, False, False
            self._noop = True
        else:
            self.error, self.amount, self.notify_budget, self.notify_queued = row
            self._noop = False

    @property
    def success(self):
        return not self._noop and self.error is None

    @property
    def redeemed(self):
        return self.amount > 0

def _give(conn, giver, recipient, ts, text=None, backdate_days=0):
    """Give kudos and optionally backdate it to bypass the daily cap."""
    row = conn.execute(
        "SELECT * FROM give_kudos(%s, %s, 'C1', %s, %s)",
        (giver, recipient, ts, text)).fetchone()
    if backdate_days > 0:
        conn.execute(
            "UPDATE kudos SET created_at = created_at - INTERVAL '%s days' WHERE message_ts = %s",
            (backdate_days, ts))
    return GiveResult(row)


# ============================================================
# §3.1 — Giving kudos: validation rules
# ============================================================


def test_success(conn):
    r = _give(conn, "U1", "U2", "1.001", text="great work on the demo")
    assert r.success is True
    assert r.error is None
    data = conn.execute("SELECT giver_id, recipient_id, message_text FROM kudos").fetchone()
    assert data == ("U1", "U2", "great work on the demo")

def test_self_kudos_blocked(conn):
    r = _give(conn, "U1", "U1", "1.001")
    assert r.success is False
    assert "yourself" in r.error.lower()

def test_self_kudos_check_constraint(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO kudos (giver_id, recipient_id, channel_id, message_ts) "
            "VALUES ('U1', 'U1', 'C1', '1.001')")
    conn.rollback()

def test_daily_cap(conn):
    _give(conn, "U1", "U2", "1.001")
    r = _give(conn, "U1", "U3", "1.002")
    assert r.success is False
    assert "today" in r.error.lower()

def test_daily_cap_resets_next_day(conn):
    _give(conn, "U1", "U2", "1.001", backdate_days=1)
    r = _give(conn, "U1", "U3", "1.002")
    assert r.success is True

def test_monthly_per_pair_cap(conn):
    _give(conn, "U1", "U2", "1.001", backdate_days=2)
    r = _give(conn, "U1", "U2", "1.002")
    assert r.success is False
    assert "this person this month" in r.error.lower()

def test_different_givers_same_recipient_same_day(conn):
    r1 = _give(conn, "U1", "U3", "1.001")
    r2 = _give(conn, "U2", "U3", "1.002")
    assert r1.success is True
    assert r2.success is True

def test_duplicate_message_ts_is_noop(conn):
    _give(conn, "U1", "U2", "1.001", backdate_days=2)
    r = _give(conn, "U1", "U3", "1.001")
    assert r.success is False
    assert r._noop is True

def test_deleted_kudos_does_not_block_new_kudos(conn):
    _give(conn, "U1", "U2", "1.001", backdate_days=2)
    conn.execute("SELECT * FROM delete_kudos('C1', '1.001')")
    r = _give(conn, "U1", "U2", "1.002")
    assert r.success is True


# ============================================================
# §3.2 — Auto-redemption: received >= given
# ============================================================


def test_redeems_when_received_ge_given(conn):
    _set_budget(conn, points=100, rate=5.0)
    _give(conn, "U2", "U1", "1.001", backdate_days=2)
    r = _give(conn, "U1", "U3", "1.002")
    assert r.success is True
    assert r.redeemed is True
    assert r.amount == 5.0

def test_no_redeem_when_received_lt_given(conn):
    _set_budget(conn, points=100, rate=5.0)
    r = _give(conn, "U1", "U2", "1.001")
    assert r.success is True
    assert r.redeemed is False

def test_no_redeem_without_budget(conn):
    _give(conn, "U2", "U1", "1.001", backdate_days=2)
    r = _give(conn, "U1", "U3", "1.002")
    assert r.success is True
    assert r.redeemed is False

def test_redeems_multiple_times_with_surplus(conn):
    """If received > given, each subsequent give also redeems."""
    _set_budget(conn, points=100, rate=5.0)
    _give(conn, "U2", "U1", "1.001", backdate_days=3)
    _give(conn, "U3", "U1", "1.002", backdate_days=2)
    r1 = _give(conn, "U1", "U4", "1.003", backdate_days=1)
    r2 = _give(conn, "U1", "U5", "1.004")
    assert r1.redeemed is True
    assert r2.redeemed is True

def test_no_redeem_once_surplus_exhausted(conn):
    """Once given > received, no more redemptions."""
    _set_budget(conn, points=100, rate=5.0)
    _give(conn, "U2", "U1", "1.001", backdate_days=3)
    _give(conn, "U1", "U3", "1.002", backdate_days=1)
    r = _give(conn, "U1", "U4", "1.003")
    assert r.redeemed is False

def test_budget_exhaustion_notification(conn):
    _set_budget(conn, points=1, rate=5.0)
    _give(conn, "U2", "U1", "1.001", backdate_days=2)
    r = _give(conn, "U1", "U3", "1.002")
    assert r.redeemed is True
    assert r.notify_budget is True

def test_budget_exhaustion_does_not_re_notify(conn):
    """Second redemption attempt after budget is full should not re-notify."""
    _set_budget(conn, points=1, rate=5.0)
    _give(conn, "U2", "U1", "1.001", backdate_days=2)
    _give(conn, "U1", "U3", "1.002", backdate_days=1)  # fills budget
    _give(conn, "U4", "U5", "1.003", backdate_days=1)
    r = _give(conn, "U5", "U6", "1.004")
    assert r.notify_budget is False

def test_queued_user_notified_first_time(conn):
    _set_budget(conn, points=1, rate=5.0)
    _give(conn, "U2", "U1", "1.001", backdate_days=2)
    _give(conn, "U1", "U3", "1.002", backdate_days=1)  # fills budget
    _give(conn, "U3", "U5", "1.003", backdate_days=1)
    r = _give(conn, "U5", "U6", "1.004")
    assert r.redeemed is False
    assert r.notify_queued is True

def test_queued_user_not_re_notified(conn):
    """Same user queued twice should only be notified once (count > 1)."""
    _set_budget(conn, points=1, rate=5.0)
    _give(conn, "U2", "U1", "1.001", backdate_days=3)
    _give(conn, "U1", "U3", "1.002", backdate_days=2)  # fills budget
    _give(conn, "U3", "U5", "1.003", backdate_days=2)
    _give(conn, "U5", "U6", "1.004", backdate_days=1)  # first queue
    _give(conn, "U7", "U5", "1.005", backdate_days=1)
    r = _give(conn, "U5", "U8", "1.006")  # second queue
    assert r.notify_queued is False


# ============================================================
# §3.3 — Overflow: monthly budget rollover
# ============================================================


def _overflow_redeem(conn):
    """Run the same SQL as overflow.py: redeem pending up to remaining budget."""
    return conn.execute(
        "WITH to_redeem AS ("
        "    SELECT pr.kudos_id AS id"
        "    FROM pending_redemptions pr"
        "    LIMIT COALESCE(("
        "        SELECT GREATEST(0, eb.point_budget - redeemed_this_month())"
        "        FROM effective_budget() eb), 0))"
        " UPDATE kudos SET redeemed_at = NOW()"
        "    FROM to_redeem WHERE kudos.id = to_redeem.id").rowcount

def test_overflow_redeems_queued_points(conn):
    """Queued points from a full budget get redeemed by overflow."""
    _set_budget(conn, points=1, rate=5.0)
    # U2→U1 so U1 has received=1
    _give(conn, "U2", "U1", "1.001", backdate_days=3)
    # U1→U3 redeems (fills budget=1)
    r = _give(conn, "U1", "U3", "1.002", backdate_days=2)
    assert r.redeemed is True
    # U4→U5 so U5 has received=1
    _give(conn, "U4", "U5", "1.003", backdate_days=2)
    # U5→U6 can't redeem (budget full + overflow pending from U1)
    r = _give(conn, "U5", "U6", "1.004", backdate_days=1)
    assert r.redeemed is False
    # New month budget
    conn.execute(
        "INSERT INTO budgets (month_date, point_budget, conversion_rate) "
        "VALUES ((date_trunc('month', CURRENT_DATE) + interval '1 month')::date, 10, 5.0)")
    # Simulate next month by shifting redeemed_at to last month
    conn.execute(
        "UPDATE kudos SET redeemed_at = redeemed_at - interval '1 month' "
        "WHERE redeemed_at IS NOT NULL")
    count = _overflow_redeem(conn)
    assert count == 1
    # Verify U5's kudos got redeemed
    redeemed = conn.execute(
        "SELECT COUNT(*)::int FROM kudos WHERE redeemed_at IS NOT NULL AND deleted_at IS NULL"
    ).fetchone()[0]
    assert redeemed == 2

def test_overflow_respects_remaining_budget(conn):
    """Overflow should only use remaining budget, not the full budget."""
    _set_budget(conn, points=3, rate=5.0)
    # Create 3 pending redemptions by inserting directly
    for i, (g, r) in enumerate([("U1", "U2"), ("U3", "U4"), ("U5", "U6")]):
        conn.execute(
            "INSERT INTO kudos (giver_id, recipient_id, channel_id, message_ts, created_at) "
            "VALUES (%s, %s, 'C1', %s, NOW() - interval '10 days')",
            (g, r, f"1.{i:03d}"))
        # Give each giver a received kudos so they're owed
        conn.execute(
            "INSERT INTO kudos (giver_id, recipient_id, channel_id, message_ts, created_at) "
            "VALUES (%s, %s, 'C1', %s, NOW() - interval '10 days')",
            (r, g, f"2.{i:03d}"))
    # Manually mark 1 as already redeemed this month (simulates inline redemption)
    conn.execute(
        "UPDATE kudos SET redeemed_at = NOW() WHERE message_ts = '1.000'")
    # Overflow should redeem 2 (budget=3, already redeemed=1, remaining=2), not 3
    count = _overflow_redeem(conn)
    assert count == 2

def test_overflow_no_budget_redeems_zero(conn):
    """With no budget configured, overflow redeems nothing."""
    _give(conn, "U2", "U1", "1.001", backdate_days=2)
    _give(conn, "U1", "U3", "1.002")
    count = _overflow_redeem(conn)
    assert count == 0

def test_overflow_fifo_order(conn):
    """Overflow redeems oldest pending first."""
    _set_budget(conn, points=1, rate=5.0)
    # Two givers, each owed 1 point, created at different times
    for g, r, ts_g, ts_r, days in [
            ("U1", "U2", "1.001", "2.001", 10),
            ("U3", "U4", "1.002", "2.002", 5)]:
        conn.execute(
            "INSERT INTO kudos (giver_id, recipient_id, channel_id, message_ts, created_at) "
            "VALUES (%s, %s, 'C1', %s, NOW() - interval '%s days')", (g, r, ts_g, days))
        conn.execute(
            "INSERT INTO kudos (giver_id, recipient_id, channel_id, message_ts, created_at) "
            "VALUES (%s, %s, 'C1', %s, NOW() - interval '%s days')", (r, g, ts_r, days))
    # Overflow with budget=1: should redeem U1's (older) not U3's
    count = _overflow_redeem(conn)
    assert count == 1
    redeemed = conn.execute(
        "SELECT giver_id FROM kudos WHERE redeemed_at IS NOT NULL").fetchone()
    assert redeemed[0] == "U1"


# ============================================================
# §3.4 — Weekly reminder query
# ============================================================


def test_weekly_reminder_query_excludes_recent_givers(conn):
    """Users who gave kudos this week should not appear in the reminder query."""
    _give(conn, "U1", "U2", "1.001")
    gave = {row[0] for row in conn.execute(
        "SELECT DISTINCT giver_id FROM kudos "
        "WHERE deleted_at IS NULL AND created_at >= date_trunc('week', NOW())"
    ).fetchall()}
    assert "U1" in gave

def test_weekly_reminder_query_includes_inactive_givers(conn):
    """Users who gave kudos before this week should appear as needing a reminder."""
    _give(conn, "U1", "U2", "1.001")
    # Move it to before the current week started
    conn.execute(
        "UPDATE kudos SET created_at = date_trunc('week', NOW()) - interval '1 day' "
        "WHERE message_ts = '1.001'")
    gave = {row[0] for row in conn.execute(
        "SELECT DISTINCT giver_id FROM kudos "
        "WHERE deleted_at IS NULL AND created_at >= date_trunc('week', NOW())"
    ).fetchall()}
    assert "U1" not in gave

def test_weekly_reminder_ignores_deleted_kudos(conn):
    """Deleted kudos should not count as having given this week."""
    _give(conn, "U1", "U2", "1.001")
    conn.execute("SELECT * FROM delete_kudos('C1', '1.001')")
    gave = {row[0] for row in conn.execute(
        "SELECT DISTINCT giver_id FROM kudos "
        "WHERE deleted_at IS NULL AND created_at >= date_trunc('week', NOW())"
    ).fetchall()}
    assert "U1" not in gave


# ============================================================
# §3.5 — Delete / undo
# ============================================================


def test_inline_blocked_when_overflow_pending(conn):
    _set_budget(conn, points=1, rate=5.0)
    _give(conn, "U2", "U1", "1.001", backdate_days=3)
    _give(conn, "U1", "U3", "1.002", backdate_days=2)  # fills budget
    # U5 queued
    _give(conn, "U4", "U5", "1.003", backdate_days=2)
    r_queued = _give(conn, "U5", "U6", "1.004", backdate_days=1)
    assert r_queued.redeemed is False
    # U8 also queued — overflow pending from U5
    _give(conn, "U7", "U8", "1.005", backdate_days=1)
    r = _give(conn, "U8", "U9", "1.006")
    assert r.redeemed is False
