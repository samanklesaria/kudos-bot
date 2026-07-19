import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv
from psycopg_pool import ConnectionPool
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ACCOUNTING_CHANNEL = os.environ.get("KUDOS_ACCOUNTING_CHANNEL")
pool = ConnectionPool(os.environ["DATABASE_URL"])
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))
_name_executor = ThreadPoolExecutor(max_workers=2)

USER_MENTION_RE = re.compile(r"<@(U[A-Z0-9]+)>")

def is_concrete_praise(text):
    try:
        response = requests.post(
            f"{os.environ['CHAT_URI']}/v1/chat/completions",
            timeout=10,
            json={
                "messages": [
                    {"role": "system", "content": (
                        "You are a content classifier. The user will provide a "
                        "kudos message. Does it praise someone for a specific, concrete "
                        "action they took? Reply with only YES or NO.")},
                    {"role": "user", "content": text}],
                "max_tokens": 5}).json()
        return response["choices"][0]["message"]["content"].strip().upper().startswith("YES")
    except Exception:
        logger.warning("LLM content gate failed, assuming good faith")
        return True

def _get_display_name(user_id):
    try:
        info = app.client.users_info(user=user_id)
        return info["user"]["profile"].get("display_name") or info["user"]["real_name"]
    except Exception:
        logger.warning("Failed to fetch display name for %s", user_id)
        return user_id

def _give_kudos_db(conn, giver_id, recipient_id, channel_id, message_ts, text, giver_name, recipient_name):
    for uid, name in [(giver_id, giver_name), (recipient_id, recipient_name)]:
        conn.execute(
            "INSERT INTO users (id, display_name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
            (uid, name))
    return conn.execute(
        "SELECT * FROM give_kudos(%s, %s, %s, %s, %s)",
        (giver_id, recipient_id, channel_id, message_ts, text)).fetchone()

def _parse_kudos(text, bot_user_id):
    """Extract the single recipient from a kudos message, or return an error string."""
    recipients = [uid for uid in USER_MENTION_RE.findall(text) if uid != bot_user_id]
    if not recipients:
        return None, "Tag someone to give them kudos! Example: `@kudos-bot @someone Great presentation on sandwich-making today!`"
    if len(recipients) > 1:
        return None, "Please give kudos to one person at a time."
    return recipients[0], None

def _handle_kudos(giver_id, channel_id, message_ts, text, bot_user_id, *, delete_first=False):
    if delete_first:
        with pool.connection() as conn:
            _delete_kudos(conn, channel_id, message_ts)
    recipient, error = _parse_kudos(text, bot_user_id)
    if error:
        if not delete_first:
            app.client.chat_postMessage(channel=channel_id, text=error, thread_ts=message_ts)
        return
    if not is_concrete_praise(text):
        app.client.chat_postMessage(
            channel=channel_id,
            text="Your kudos needs to mention a specific action. Please edit your message to be more specific. "
                 "Example: `@kudos @someone Great job leading the incident retro today!`",
            thread_ts=message_ts)
        return
    giver_name, recipient_name = _name_executor.map(_get_display_name, [giver_id, recipient])
    with pool.connection() as conn:
        row = _give_kudos_db(conn, giver_id, recipient, channel_id, message_ts, text, giver_name, recipient_name)
    if row is None: # if the entry already exists
        return
    error, rate, redeemed_ids, notify_budget = row
    if error:
        app.client.chat_postMessage(channel=channel_id, text=error, thread_ts=message_ts)
        return
    msg = f"<@{giver_id}> gave kudos to <@{recipient}>!"
    if redeemed_ids:
        redeemed_str = " and ".join(f"<@{uid}>" for uid in redeemed_ids)
        msg += f" ({redeemed_str} auto-redeemed 1 point — ${rate:.2f})"
    app.client.chat_postMessage(channel=channel_id, text=msg, thread_ts=message_ts)
    if notify_budget and ACCOUNTING_CHANNEL:
        app.client.chat_postMessage(
            channel=ACCOUNTING_CHANNEL,
            text="Budget alert: This month's kudos budget has been exhausted.")

@app.event("app_mention")
def handle_mention(event, context):
    if "edited" in event:
        return
    _handle_kudos(event["user"], event["channel"], event["ts"],
        event.get("text", ""), context.bot_user_id)

@app.event({"type": "message", "subtype": "message_changed"})
def handle_message_changed(event, context):
    message = event.get("message", {})
    text = message.get("text", "")
    if text == event.get("previous_message", {}).get("text", ""):
        return
    if f"<@{context.bot_user_id}>" not in text:
        with pool.connection() as conn:
            _delete_kudos(conn, event["channel"], message.get("ts"))
        return
    giver_id = message.get("user")
    if not giver_id:
        return
    _handle_kudos(giver_id, event["channel"], message.get("ts"),
        text, context.bot_user_id, delete_first=True)

def _delete_kudos(conn, channel_id, message_ts):
    conn.execute("CALL delete_kudos(%s, %s)", (channel_id, message_ts))

@app.event({"type": "message", "subtype": "message_deleted"})
def handle_message_deleted(event):
    message_ts = event.get("previous_message", {}).get("ts")
    if not message_ts:
        return
    with pool.connection() as conn:
        _delete_kudos(conn, event["channel"], message_ts)

@app.event("member_joined_channel")
def handle_member_joined(event, context):
    if event.get("user") != context.bot_user_id:
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
             "`@kudos @someone Great job leading the incident retro today!`")

@app.event("message")
def handle_message_default():
    pass

if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
