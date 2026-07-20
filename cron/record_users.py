"""Cron script: record weekly covariates (num_users, workday_frac)."""
import os
from datetime import date, timedelta
import holidays
import psycopg
from dotenv import load_dotenv
from slack_sdk import WebClient
from cron import paginate, get_team_id

load_dotenv()

def main():
    client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    # ── num_users: unique members across all bot channels ─────────────────
    bot_id = client.auth_test()["user_id"]
    team_id = get_team_id(client)
    users = set()
    kwargs = {"types": "public_channel"}
    if team_id:
        kwargs["team_id"] = team_id
    for ch in paginate(client.users_conversations, "channels", **kwargs):
        users.update(paginate(client.conversations_members, "members", channel=ch["id"]))
    users.discard(bot_id)
    # ── workday_frac: non-holiday weekdays in current ISO week / 5 ───────
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    us_holidays = holidays.US(years=sorted({monday.year, friday.year}))
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
