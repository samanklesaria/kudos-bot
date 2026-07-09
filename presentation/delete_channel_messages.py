"""Delete all messages from a Slack channel by name."""
import os
import time
import sys

from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

CHANNEL_NAME = sys.argv[1] if len(sys.argv) > 1 else "team-awesome"

client = WebClient(token=os.environ["SLACK_USER_TOKEN"])

def find_channel(name):
    for page in client.conversations_list(types="public_channel,private_channel", limit=200):
        for ch in page["channels"]:
            if ch["name"] == name:
                return ch["id"]
    raise SystemExit(f"Channel '{name}' not found")

def delete_all_messages(channel_id):
    deleted = 0
    cursor = None
    while True:
        resp = client.conversations_history(
            channel=channel_id, limit=200,
            **({} if cursor is None else {"cursor": cursor}))
        for msg in resp["messages"]:
            try:
                client.chat_delete(channel=channel_id, ts=msg["ts"])
                deleted += 1
                # ponytail: rate limit at 1/sec, tier 3 limit is ~50/min but this is safe enough
                time.sleep(1)
            except SlackApiError as e:
                print(f"skip {msg['ts']}: {e.response['error']}")
        print(f"deleted {deleted} so far...")
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    print(f"done. {deleted} messages deleted from #{CHANNEL_NAME}")

channel_id = find_channel(CHANNEL_NAME)
print(f"found #{CHANNEL_NAME} ({channel_id}), deleting all messages...")
delete_all_messages(channel_id)
