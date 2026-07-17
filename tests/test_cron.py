import os
from pathlib import Path
from unittest.mock import patch

import psycopg
import pytest

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
DB_URL = os.environ.get("KUDOS_TEST_DATABASE_URL", "postgresql://localhost/kudos_test")


@pytest.fixture
def conn():
    c = psycopg.connect(DB_URL)
    c.execute("DROP SCHEMA IF EXISTS public CASCADE")
    c.execute("CREATE SCHEMA public")
    for sql_file in sorted(SCHEMA_DIR.glob("*.sql")):
        c.execute(sql_file.read_text())
    c.commit()
    yield c
    c.close()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    monkeypatch.setenv("KUDOS_ACCOUNTING_CHANNEL", "C_ACCT")


# ── accounting ────────────────────────────────────────────────────────────

def _seed_last_month_redemption(conn):
    """Insert a budget and a redeemed kudos dated last month."""
    conn.execute(
        "INSERT INTO budgets (month_date, point_budget, conversion_rate) "
        "VALUES ((date_trunc('month', CURRENT_DATE) - interval '1 month')::date, 100, 5.0)")
    conn.execute(
        "INSERT INTO kudos (giver_id, recipient_id, channel_id, message_ts, "
        "redeemed_at, created_at) VALUES "
        "('U1', 'U2', 'C1', '1.001', "
        "date_trunc('month', CURRENT_DATE) - interval '1 day', "
        "date_trunc('month', CURRENT_DATE) - interval '2 days')")
    conn.commit()


@patch("cron.accounting.WebClient")
def test_accounting_posts_redemptions(MockClient, conn, env):
    _seed_last_month_redemption(conn)
    client = MockClient.return_value
    client.chat_getPermalink.return_value = {"permalink": "https://slack.com/p/123"}
    from cron.accounting import main
    main()
    client.chat_postMessage.assert_called_once()
    msg = client.chat_postMessage.call_args[1]["text"]
    assert "<@U2>" in msg
    assert "$5.00" in msg


@patch("cron.accounting.WebClient")
def test_accounting_no_redemptions(MockClient, conn, env):
    conn.commit()
    client = MockClient.return_value
    from cron.accounting import main
    main()
    msg = client.chat_postMessage.call_args[1]["text"]
    assert "No redemptions" in msg


@patch("cron.accounting.WebClient")
def test_accounting_permalink_fallback(MockClient, conn, env):
    _seed_last_month_redemption(conn)
    client = MockClient.return_value
    client.chat_getPermalink.side_effect = Exception("channel_not_found")
    from cron.accounting import main
    main()
    msg = client.chat_postMessage.call_args[1]["text"]
    assert "#1 (C1)" in msg


# ── weekly_reminder ───────────────────────────────────────────────────────

def _make_members(*user_ids, bots=(), deleted=()):
    members = []
    for uid in user_ids:
        members.append({"id": uid, "is_bot": uid in bots, "deleted": uid in deleted})
    return {"members": members, "response_metadata": {"next_cursor": ""}}


@patch("cron.weekly_reminder.WebClient")
def test_reminder_skips_recent_givers(MockClient, conn, env):
    conn.execute(
        "INSERT INTO kudos (giver_id, recipient_id, channel_id, message_ts) "
        "VALUES ('U1', 'U2', 'C1', '1.001')")
    conn.commit()
    client = MockClient.return_value
    client.users_list.return_value = _make_members("U1", "U2", "U3")
    from cron.weekly_reminder import main
    main()
    reminded = {c[1]["channel"] for c in client.chat_postMessage.call_args_list}
    assert "U1" not in reminded
    assert "U2" in reminded
    assert "U3" in reminded


@patch("cron.weekly_reminder.WebClient")
def test_reminder_skips_bots(MockClient, conn, env):
    conn.commit()
    client = MockClient.return_value
    client.users_list.return_value = _make_members("U1", "UBOT", bots=("UBOT",))
    from cron.weekly_reminder import main
    main()
    reminded = {c[1]["channel"] for c in client.chat_postMessage.call_args_list}
    assert "U1" in reminded
    assert "UBOT" not in reminded


@patch("cron.weekly_reminder.WebClient")
def test_reminder_skips_slackbot(MockClient, conn, env):
    conn.commit()
    client = MockClient.return_value
    client.users_list.return_value = _make_members("U1", "USLACKBOT")
    from cron.weekly_reminder import main
    main()
    reminded = {c[1]["channel"] for c in client.chat_postMessage.call_args_list}
    assert "USLACKBOT" not in reminded
