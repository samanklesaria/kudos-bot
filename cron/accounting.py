"""Monthly cron script: report last month's redemptions to the accounting channel."""
import os

import psycopg
from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv()

client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
ACCOUNTING_CHANNEL = os.environ["KUDOS_ACCOUNTING_CHANNEL"]

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    last_month = conn.execute("SELECT * FROM last_month_redemptions").fetchall()

if last_month:
    lines = ["*Last month's redemptions by recipient:*"]
    for recipient_id, channels, timestamps, total in last_month:
        links = " ".join(
            f"<{client.chat_getPermalink(channel=c, message_ts=t)['permalink']}|#{i+1}>"
            for i, (c, t) in enumerate(zip(channels, timestamps)))
        lines.append(f"• <@{recipient_id}>: {len(channels)} point(s), ${total:.2f} — {links}")
    client.chat_postMessage(
        channel=ACCOUNTING_CHANNEL,
        text="\n".join(lines))
else:
    client.chat_postMessage(
        channel=ACCOUNTING_CHANNEL,
        text="No redemptions last month.")
