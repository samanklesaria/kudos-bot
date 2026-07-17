"""Monthly cron script: report last month's redemptions to the accounting channel."""
import os

import psycopg
from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv()

def main():
    client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    team_id = client.auth_teams_list()["teams"][0]["id"]
    accounting_channel = os.environ["KUDOS_ACCOUNTING_CHANNEL"]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        last_month = conn.execute("SELECT * FROM current_month_redemptions").fetchall()
    def _permalink(c, t):
        try:
            return f"<{client.chat_getPermalink(channel=c, message_ts=t)['permalink']}|link>"
        except Exception:
            return None
    if last_month:
        lines = ["*This month's redemptions by recipient:*"]
        for recipient_id, channels, timestamps, total in last_month:
            links = " ".join(
                filter(None, (_permalink(c, t) for c, t in zip(channels, timestamps))))
            lines.append(f"• <@{recipient_id}>: {len(channels)} point(s), ${total:.2f} — {links}")
        client.chat_postMessage(
            team_id=team_id,
            channel=accounting_channel,
            text="\n".join(lines))
    else:
        client.chat_postMessage(
            team_id=team_id,
            channel=accounting_channel,
            text="No redemptions this month.")

if __name__ == "__main__":
    main()
