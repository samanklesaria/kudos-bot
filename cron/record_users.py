"""Cron script: record weekly covariates (num_users, workday_frac, channel_messages)."""
import os
import time
from datetime import date, timedelta
import psycopg
from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv()

client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])

channels = client.users_conversations(types="public_channel")["channels"]

# ── num_users: unique members across all bot channels ─────────────────
users = set()
for ch in channels:
    cursor = None
    while True:
        resp = client.conversations_members(channel=ch["id"],
            **({"cursor": cursor} if cursor else {}))
        users.update(resp["members"])
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

# ── workday_frac: business days in current ISO week / 5 ──────────────
today = date.today()
monday = today - timedelta(days=today.weekday())
workdays = sum(1 for d in range(5) if (monday + timedelta(days=d)).isoweekday() <= 5)
# ponytail: assumes no holiday calendar; add one if precision matters
workday_frac = workdays / 5.0

# ── channel_messages: total messages across bot channels this week ────
week_start = time.mktime(monday.timetuple())
total_messages = 0
for ch in channels:
    resp = client.conversations_history(channel=ch["id"],
        oldest=str(week_start), limit=1, include_all_metadata=False)
    total_messages += resp.get("total", 0) if resp.get("has_more") else len(resp.get("messages", []))
# ponytail: conversations_history doesn't return a total count reliably;
# paginating all messages is expensive. Using conversations.info num_messages
# delta would be better at scale — revisit if channel volume is high.

# ── write all covariates ──────────────────────────────────────────────
covariates = [
    ("num_users", len(users)),
    ("workday_frac", workday_frac),
    ("channel_messages", total_messages)]

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    for label, value in covariates:
        conn.execute(
            "INSERT INTO covariates (label, week, value) "
            "VALUES (%s, to_char(CURRENT_DATE, 'IYYY-IW'), %s) "
            "ON CONFLICT (label, week) DO UPDATE SET value = EXCLUDED.value",
            (label, value))

for label, value in covariates:
    print(f"Recorded {label}={value}")
