"""Monthly cron script: report last month's redemptions to the accounting channel."""
import os

import psycopg
from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv()

def main():
    client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    accounting_channel = os.environ["KUDOS_ACCOUNTING_CHANNEL"]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        last_month = conn.execute("SELECT * FROM last_month_redemptions").fetchall()
    def _permalink(c, t, i):
        try:
            return f"<{client.chat_getPermalink(channel=c, message_ts=t)['permalink']}|#{i+1}>"
        except Exception:
            return f"#{i+1} ({c})"
    if last_month:
        lines = ["*Last month's redemptions by recipient:*"]
        for recipient_id, channels, timestamps, total in last_month:
            links = " ".join(
                _permalink(c, t, i) for i, (c, t) in enumerate(zip(channels, timestamps)))
            lines.append(f"• <@{recipient_id}>: {len(channels)} point(s), ${total:.2f} — {links}")
        client.chat_postMessage(
            channel=accounting_channel,
            text="\n".join(lines))
    else:
        client.chat_postMessage(
            channel=accounting_channel,
            text="No redemptions last month.")

if __name__ == "__main__":
    main()
