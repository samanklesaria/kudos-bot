"""Cron script: DM users who haven't given kudos this week."""
import logging
import os
import time
import psycopg
from dotenv import load_dotenv
from slack_sdk import WebClient

logger = logging.getLogger(__name__)

load_dotenv()

def _get_bot_channel_members(client, team_id):
    bot_id = client.auth_test()["user_id"]
    members = set()
    for channel in client.conversations_list(types="public_channel", team_id=team_id)["channels"]:
        members.update(client.conversations_members(channel=channel["id"])["members"])
    members.discard(bot_id)
    return members

def main():
    client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    team_id = client.auth_teams_list()["teams"][0]["id"]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        gave_this_week = {
            row[0] for row in conn.execute(
                "SELECT DISTINCT giver_id FROM kudos "
                "WHERE created_at >= date_trunc('week', NOW())"
            ).fetchall()}
    for user_id in _get_bot_channel_members(client, team_id) - gave_this_week:
        try:
            client.chat_postMessage(
                channel=user_id,
                text=(
                    "Reminder: You haven't given your kudos this week! "
                    "Recognize a colleague's great work before the week ends."))
            time.sleep(1)  # ponytail: rate limit, increase if hitting 429s
        except Exception:
            logger.warning("Failed to DM %s", user_id)

if __name__ == "__main__":
    main()
