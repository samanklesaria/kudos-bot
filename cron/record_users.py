"""Cron script: record weekly covariates (num_users, workday_frac)."""
import os
from datetime import date, timedelta
import holidays
import psycopg
from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv()

def main():
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
    # ── workday_frac: non-holiday weekdays in current ISO week / 5 ───────
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    us_holidays = holidays.US(years=today.year)
    workdays = sum(1 for d in range(5)
        if (day := monday + timedelta(days=d)) not in us_holidays)
    workday_frac = workdays / 5.0
    # ── write all covariates ──────────────────────────────────────────────
    covariates = [
        ("num_users", len(users)),
        ("workday_frac", workday_frac)]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for label, value in covariates:
            conn.execute(
                "INSERT INTO covariates (label, week, value) "
                "VALUES (%s, to_char(CURRENT_DATE, 'IYYY-IW'), %s) "
                "ON CONFLICT (label, week) DO UPDATE SET value = EXCLUDED.value",
                (label, value))
    for label, value in covariates:
        print(f"Recorded {label}={value}")

if __name__ == "__main__":
    main()
