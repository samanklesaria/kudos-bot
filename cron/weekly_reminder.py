"""Cron script: DM users who haven't given kudos this week."""
import os
import psycopg
from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv()

def _paginate(method, key, **kwargs):
    cursor = None
    while True:
        resp = method(limit=200, **({"cursor": cursor} if cursor else {}), **kwargs)
        yield from resp[key]
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

def _get_bot_channel_members(client, team_id):
    bot_id = client.auth_test()["user_id"]
    members = set()
    for channel in _paginate(client.conversations_list, "channels", types="public_channel", team_id=team_id):
        members.update(_paginate(client.conversations_members, "members", channel=channel["id"]))
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
        client.chat_postMessage(
            channel=user_id,
            text=(
                "Reminder: You haven't given your kudos this week! "
                "Recognize a colleague's great work before the week ends."))

if __name__ == "__main__":
    main()
