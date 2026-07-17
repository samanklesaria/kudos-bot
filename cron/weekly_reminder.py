"""Cron script: DM users who haven't given kudos this week."""
import os
import psycopg
from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv()

def main():
    client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        gave_this_week = {
            row[0] for row in conn.execute(
                "SELECT DISTINCT giver_id FROM kudos "
                "WHERE deleted_at IS NULL AND created_at >= date_trunc('week', NOW())"
            ).fetchall()}
    cursor = None
    while True:
        resp = client.users_list(limit=200, **({"cursor": cursor} if cursor else {}))
        for member in resp["members"]:
            if (member["is_bot"] or member.get("deleted") or member["id"] == "USLACKBOT"
                    or member.get("is_restricted") or member.get("is_ultra_restricted")):
                continue
            if member["id"] not in gave_this_week:
                client.chat_postMessage(
                    channel=member["id"],
                    text=(
                        "Reminder: You haven't given your kudos this week! "
                        "Recognize a colleague's great work before the week ends."))
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

if __name__ == "__main__":
    main()
