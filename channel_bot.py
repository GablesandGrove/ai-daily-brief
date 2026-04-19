#!/usr/bin/env python3
"""
channel_bot.py — Slack bot that watches for YouTube URLs and adds channels to channels.json.

Run with: python channel_bot.py
Then drop any YouTube video or channel URL into #ai-daily-brief.
"""

import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import build
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(level=logging.INFO)

load_dotenv()

CHANNELS_FILE = Path(__file__).parent / "channels.json"
YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]

app = App(token=SLACK_BOT_TOKEN)

YOUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|channel/|@[\w-]+/?)|youtu\.be/)([\w\-@/]+)"
)


def load_channels() -> list[dict]:
    with open(CHANNELS_FILE) as f:
        return json.load(f)


def save_channels(channels: list[dict]) -> None:
    with open(CHANNELS_FILE, "w") as f:
        json.dump(channels, f, indent=2)
    print(f"Saved {len(channels)} channels to {CHANNELS_FILE}")


def get_channel_id_from_video(youtube, video_id: str) -> tuple[str, str] | None:
    """Return (channel_id, channel_title) from a video ID."""
    resp = youtube.videos().list(part="snippet", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        return None
    snippet = items[0]["snippet"]
    return snippet["channelId"], snippet["channelTitle"]


def get_channel_info_from_handle(youtube, handle: str) -> tuple[str, str] | None:
    """Return (channel_id, channel_title) from a @handle or channel ID."""
    handle = handle.lstrip("@")
    resp = (
        youtube.search()
        .list(part="snippet", q=handle, type="channel", maxResults=1)
        .execute()
    )
    items = resp.get("items", [])
    if not items:
        return None
    snippet = items[0]["snippet"]
    return snippet["channelId"], snippet["channelTitle"]


def resolve_youtube_url(url: str) -> tuple[str, str] | None:
    """Return (channel_id, channel_title) from any YouTube URL, or None."""
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

    # Video URL: youtube.com/watch?v=VIDEO_ID or youtu.be/VIDEO_ID
    video_match = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", url)
    if video_match:
        return get_channel_id_from_video(youtube, video_match.group(1))

    # Channel URL: youtube.com/channel/UC...
    channel_id_match = re.search(r"channel/(UC[\w-]+)", url)
    if channel_id_match:
        channel_id = channel_id_match.group(1)
        resp = youtube.channels().list(part="snippet", id=channel_id).execute()
        items = resp.get("items", [])
        if items:
            return channel_id, items[0]["snippet"]["title"]

    # Handle URL: youtube.com/@handle
    handle_match = re.search(r"youtube\.com/@([\w-]+)", url)
    if handle_match:
        return get_channel_info_from_handle(youtube, handle_match.group(1))

    return None


def process_youtube_message(text: str, say) -> None:
    """Extract YouTube URLs from text, resolve channels, update channels.json."""
    full_urls = re.findall(
        r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=[\w-]+|channel/UC[\w-]+|@[\w-]+/?)|youtu\.be/[\w-]+)",
        text,
    )
    if not full_urls:
        return

    channels = load_channels()
    existing_ids = {ch["channel_id"] for ch in channels}
    added = []
    skipped = []

    for url in full_urls:
        result = resolve_youtube_url(url)
        if not result:
            say(f"⚠️ Couldn't resolve a channel from: {url}")
            continue

        channel_id, channel_title = result

        if channel_id in existing_ids:
            skipped.append(channel_title)
            continue

        channels.append({"channel_id": channel_id, "label": channel_title})
        existing_ids.add(channel_id)
        added.append(channel_title)

    if added:
        save_channels(channels)
        names = ", ".join(f"*{n}*" for n in added)
        say(f"✅ Added {names} — now monitoring *{len(channels)}* channels.")

    for name in skipped:
        say(f"ℹ️ *{name}* is already being monitored.")


@app.event("message")
def handle_message_events(body, say, logger):
    """Handle all message events (public and private channels)."""
    event = body.get("event", {})
    subtype = event.get("subtype")

    # Ignore edits, deletions, and bot messages
    if subtype in ("message_changed", "message_deleted", "bot_message"):
        return

    text = event.get("text", "")
    logger.info(f"Message received: {text[:80]}")

    if YOUTUBE_URL_RE.search(text):
        process_youtube_message(text, say)


if __name__ == "__main__":
    print("Channel bot running — drop YouTube URLs into Slack to add channels.")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
