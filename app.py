import logging
import os
import re

import requests
from dotenv import load_dotenv
from psycopg_pool import ConnectionPool
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ACCOUNTING_CHANNEL = os.environ.get("KUDOS_ACCOUNTING_CHANNEL")
CHAT_URI = os.environ["CHAT_URI"]
DATABASE_URL = os.environ["DATABASE_URL"]
pool = ConnectionPool(DATABASE_URL)
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

USER_MENTION_RE = re.compile(r"<@(U[A-Z0-9]+)>")

def is_concrete_praise(text):
    try:
        response = requests.post(
            f"{CHAT_URI}/v1/chat/completions",
            timeout=10,
            json={
                "messages": [
                    {"role": "system", "content": (
                        "You are a content classifier. The user will provide a workplace "
                        "kudos message. Does it praise someone for a specific, concrete, "
                        "demonstrable action they took? Reply with only YES or NO.")},
                    {"role": "user", "content": text}],
                "max_tokens": 5}).json()
        return response["choices"][0]["message"]["content"].strip().upper().startswith("YES")
    except Exception:
        return True  # assume good faith if LLM is down


def _try_give_kudos(giver_id, recipient_id, channel_id, message_ts, text):
    if not is_concrete_praise(text):
        app.client.chat_postMessage(
            channel=channel_id,
            text="Your kudos needs to mention a specific action. Please edit your message to be more specific. "
                 "Example: `@kudos-bot @someone Great job leading the incident retro today!`",
            thread_ts=message_ts)
        return
    giver_info = app.client.users_info(user=giver_id)
    giver_name = giver_info["user"]["profile"].get("display_name") or giver_info["user"]["real_name"]
    recipient_info = app.client.users_info(user=recipient_id)
    recipient_name = recipient_info["user"]["profile"].get("display_name") or recipient_info["user"]["real_name"]
    with pool.connection() as conn:
        for uid, name in [(giver_id, giver_name), (recipient_id, recipient_name)]:
            conn.execute(
                "INSERT INTO users (id, display_name) VALUES (%s, %s) "
                "ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
                (uid, name))
        row = conn.execute(
            "SELECT * FROM give_kudos(%s, %s, %s, %s, %s)",
            (giver_id, recipient_id, channel_id, message_ts, text)).fetchone()
    if row is None:
        return
    error, redeemed_amount, notify_budget, notify_queued = row
    if error:
        app.client.chat_postMessage(channel=channel_id, text=error, thread_ts=message_ts)
        return
    msg = f"<@{giver_id}> gave kudos to <@{recipient_id}>!"
    if redeemed_amount > 0:
        msg += f" (<@{giver_id}> auto-redeemed 1 point — ${redeemed_amount:.2f})"
    app.client.chat_postMessage(channel=channel_id, text=msg, thread_ts=message_ts)
    if notify_queued:
        app.client.chat_postMessage(
            channel=channel_id,
            text="Your kudos payout is queued because this month's budget is exhausted. "
                 "It will be processed when budget is available.",
            thread_ts=message_ts)
    if notify_budget and ACCOUNTING_CHANNEL:
        app.client.chat_postMessage(
            channel=ACCOUNTING_CHANNEL,
            text=f"Budget alert: This month's kudos budget has been exhausted. "
                 f"New redemptions are being queued.")

@app.event("app_mention")
def handle_mention(event, context):
    giver_id = event["user"]
    message_ts = event["ts"]
    text = event.get("text", "")
    bot_user_id = context["bot_user_id"]
    recipients = [uid for uid in USER_MENTION_RE.findall(text) if uid != bot_user_id]
    if not recipients:
        app.client.chat_postMessage(
            channel=event["channel"],
            text="Tag someone to give them kudos! Example: `@kudos-bot @someone Great presentation on sandwich-making today!`",
            thread_ts=message_ts)
        return
    if len(recipients) > 1:
        app.client.chat_postMessage(
            channel=event["channel"],
            text="Please give kudos to one person at a time.",
            thread_ts=message_ts)
        return
    _try_give_kudos(giver_id, recipients[0], event["channel"], message_ts, text)

@app.event({"type": "message", "subtype": "message_changed"})
def handle_message_changed(event, context):
    message = event.get("message", {})
    text = message.get("text", "")
    bot_user_id = context["bot_user_id"]
    if f"<@{bot_user_id}>" not in text:
        return
    giver_id = message.get("user")
    if not giver_id:
        return
    message_ts = message.get("ts")
    channel_id = event["channel"]
    recipients = [uid for uid in USER_MENTION_RE.findall(text) if uid != bot_user_id]
    if len(recipients) != 1:
        return
    with pool.connection() as conn:
        row = _delete_kudos(conn, channel_id, message_ts)
    _notify_redeemed_deletion(row)
    _try_give_kudos(giver_id, recipients[0], channel_id, message_ts, text)

def _delete_kudos(conn, channel_id, message_ts):
    row = conn.execute(
        "SELECT * FROM delete_kudos(%s, %s)", (channel_id, message_ts)).fetchone()
    return row

def _notify_redeemed_deletion(row):
    if row and row[0] and ACCOUNTING_CHANNEL:
        app.client.chat_postMessage(
            channel=ACCOUNTING_CHANNEL,
            text=(
                f"Warning: Kudos deletion for <@{row[1]}> involved "
                f"an already-redeemed point. This may require manual review."))

@app.event({"type": "message", "subtype": "message_deleted"})
def handle_message_deleted(event):
    message_ts = event.get("previous_message", {}).get("ts")
    if not message_ts:
        return
    with pool.connection() as conn:
        row = _delete_kudos(conn, event["channel"], message_ts)
    _notify_redeemed_deletion(row)

@app.event("member_joined_channel")
def handle_member_joined(event, context):
    if event.get("user") != context["bot_user_id"]:
        return
    channel_id = event["channel"]
    info = app.client.conversations_info(channel=channel_id)
    if info["channel"]["is_private"]:
        app.client.chat_postMessage(
            channel=channel_id,
            text="Sorry, I only monitor kudos in public channels. Leaving this channel now.")
        app.client.conversations_leave(channel=channel_id)
        return
    app.client.chat_postMessage(
        channel=channel_id,
        text="Hi! I'm the kudos bot. Recognize a colleague by mentioning me: "
             "`@kudos-bot @someone Great job leading the incident retro today!`")

@app.event("message")
def handle_message_default():
    pass

if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
