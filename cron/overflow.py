"""Monthly cron script: process overflow kudos redemptions against the current budget."""
import os

import psycopg
from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv()

client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
ACCOUNTING_CHANNEL = os.environ.get("KUDOS_ACCOUNTING_CHANNEL")
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(0)")
        last_month = conn.execute("SELECT * FROM last_month_redemptions").fetchall()
        queued_points = conn.execute(
            "WITH to_redeem AS ("
            "    SELECT pr.kudos_id AS id"
            "    FROM pending_redemptions pr"
            "    LIMIT COALESCE(("
            "        SELECT GREATEST(0, eb.point_budget - redeemed_this_month())"
            "        FROM effective_budget() eb), 0))"
            " UPDATE kudos SET redeemed_at = NOW()"
            "    FROM to_redeem WHERE kudos.id = to_redeem.id").rowcount

if last_month and ACCOUNTING_CHANNEL:
    lines = ["*Last month's redemptions by recipient:*"]
    for recipient_id, channels, timestamps, total in last_month:
        links = " ".join(
            f"<{client.chat_getPermalink(channel=c, message_ts=t)['permalink']}|#{i+1}>"
            for i, (c, t) in enumerate(zip(channels, timestamps)))
        lines.append(f"• <@{recipient_id}>: {len(channels)} point(s), ${total:.2f} — {links}")
    client.chat_postMessage(
        channel=ACCOUNTING_CHANNEL,
        text="\n".join(lines))

if queued_points > 0 and ACCOUNTING_CHANNEL:
    client.chat_postMessage(
        channel=ACCOUNTING_CHANNEL,
        text=f"Rollover alert: {queued_points} queued point(s) from previous months "
             f"were processed against this month's budget.")
