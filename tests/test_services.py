import os
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
    def __init__(self, row, giver=None, recipient=None):
        self._noop = row is None
        self.error, self.rate, self.redeemed_ids, self.notify_budget = row or (None, 0, [], False)
        self._giver, self._recipient = giver, recipient

    @property
    def success(self):
        return not self._noop and self.error is None

    @property
    def redeemed(self):
        return bool(self.redeemed_ids)

    @property
    def giver_amount(self):
        return self.rate if self._giver in self.redeemed_ids else 0

    @property
    def recipient_amount(self):
        return self.rate if self._recipient in self.redeemed_ids else 0

def _give(conn, giver, recipient, ts, text=None, backdate_days=0):
    """Give kudos and optionally backdate it to bypass the daily cap."""
    row = conn.execute(
        "SELECT * FROM give_kudos(%s, %s, 'C1', %s, %s)",
        (giver, recipient, ts, text)).fetchone()
    if backdate_days > 0:
        conn.execute(
            "UPDATE kudos SET created_at = created_at - %s * INTERVAL '1 day' WHERE message_ts = %s",
            (backdate_days, ts))
    return GiveResult(row, giver, recipient)


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


def test_redeems_when_received_ge_given(conn):
    _set_budget(conn, points=100, rate=5.0)
    _give(conn, "U2", "U1", "1.001", backdate_days=2)
    r = _give(conn, "U1", "U3", "1.002")
    assert r.success is True
    assert r.redeemed is True
    assert r.giver_amount == 5.0

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



def test_recipient_auto_redeems_when_receiving_tips_balance(conn):
    """Receiving a kudos can trigger redemption of recipient's oldest giving row."""
    _set_budget(conn, points=100, rate=5.0)
    # U1 gives to U2 (U1 not owed, no redemption)
    _give(conn, "U1", "U2", "1.001", backdate_days=2)
    # U2 gives to U3 — U2 now has given=1. But received=1 (from 1.001). So U2 is owed!
    r = _give(conn, "U2", "U3", "1.002", backdate_days=1)
    # U2's give should auto-redeem (giver path)
    assert r.giver_amount == 5.0

def test_recipient_redeems_via_new_received_kudos(conn):
    """When recipient gets a kudos, if they're owed, their giving row redeems."""
    _set_budget(conn, points=100, rate=5.0)
    # U2 gives to U3 — U2 has given=1, received=0, not owed
    _give(conn, "U2", "U3", "1.001", backdate_days=2)
    # U1 gives to U2 — U2 now has received=1. U2 is owed 1 (given=1, received=1).
    # Recipient-path should redeem U2's giving row.
    r = _give(conn, "U1", "U2", "1.002")
    assert r.recipient_amount == 5.0
    # Verify U2's received row (1.002) got redeemed
    redeemed = conn.execute(
        "SELECT redeemed_at IS NOT NULL FROM kudos WHERE message_ts = '1.002'").fetchone()[0]
    assert redeemed is True


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


def test_edit_from_vague_to_specific(conn):
    """Deleting a vague kudos and re-giving with specific text should succeed."""
    _set_budget(conn, points=100, rate=5.0)
    r1 = _give(conn, "U1", "U2", "1.001", text="good job")
    assert r1.success is True  # DB doesn't enforce content; LLM gate is in app.py
    # Simulate edit: delete old, re-give with new text
    conn.execute("SELECT * FROM delete_kudos('C1', '1.001')")
    deleted = conn.execute(
        "SELECT deleted_at IS NOT NULL FROM kudos WHERE message_ts = '1.001'").fetchone()[0]
    assert deleted is True
    r2 = _give(conn, "U1", "U2", "1.002", text="great job leading the incident retro")
    assert r2.success is True


def test_global_redemption_clears_owed_eagerly(conn):
    """try_redeem redeems all owed users globally, so no stale backlog accumulates."""
    _set_budget(conn, points=100, rate=5.0)
    # U4→U3, then U3→U5: after U3's give, U3 is owed and redeemed immediately
    _give(conn, "U4", "U3", "1.001", backdate_days=3)
    r = _give(conn, "U3", "U5", "1.002", backdate_days=2)
    assert "U3" in r.redeemed_ids
    # U1→U2: U3 is already redeemed, so only U1/U2 matter
    r2 = _give(conn, "U1", "U2", "1.003")
    assert "U3" not in r2.redeemed_ids


def test_delete_kudos(conn):
    _give(conn, "U1", "U2", "1.001")
    row = conn.execute("SELECT * FROM delete_kudos('C1', '1.001')").fetchone()
    assert row is not None
    assert row[0] is False  # was_redeemed
    deleted = conn.execute(
        "SELECT deleted_at IS NOT NULL FROM kudos WHERE message_ts = '1.001'").fetchone()[0]
    assert deleted is True
