#!/usr/bin/env python3
"""
signal_scout.py — YouTube channel monitor that summarizes recent videos via Claude and posts to Slack.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import anthropic
import requests
from dotenv import load_dotenv
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

load_dotenv()

YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

INTEL_PROMPT = (
    "You are an AI intel analyst for a painting contractor company called Gables & Grove "
    "and their AI division called AFC (AI for Contractors). The team uses: Sora, VEO, Manus, "
    "Wispr Flow, Midjourney, Dribbble, GetDesign.md, Claude, Claude Code, and Claude Code "
    "Co-work. They do AI marketing, AI design, and website building for contractors.\n\n"
    "Summarize this YouTube video transcript in 3 bullets covering what's new or actionable. "
    "List any tools or products mentioned by name.\n\n"
    "Then answer: Is anything in this video directly relevant to the team's current stack or "
    "workflow? Is there a new tool, technique, or trend they should evaluate? "
    "Would this be useful for a painting contractor adopting AI, or for teaching a marketing "
    "team how to use AI?\n\n"
    "End with one of these tags:\n"
    "🔴 MUST WATCH — directly actionable for the team\n"
    "🟡 WORTH NOTING — relevant trend or tool to keep an eye on\n"
    "⚪ FYI ONLY — general AI content, low immediate relevance\n\n"
    "Be concise and direct."
)


def load_channels(path: str = "channels.json") -> list[dict]:
    with open(path) as f:
        return json.load(f)


def get_recent_videos(youtube, channel_id: str, hours: int = 24) -> list[dict]:
    """Return videos published in the last `hours` hours for a channel."""
    published_after = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    response = (
        youtube.search()
        .list(
            part="id,snippet",
            channelId=channel_id,
            publishedAfter=published_after,
            type="video",
            order="date",
            maxResults=10,
        )
        .execute()
    )
    videos = []
    for item in response.get("items", []):
        videos.append(
            {
                "video_id": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "channel_title": item["snippet"]["channelTitle"],
                "published_at": item["snippet"]["publishedAt"],
                "url": f"https://www.youtube.com/watch?v={item['id']['videoId']}",
            }
        )
    return videos


def get_transcript(video_id: str) -> str | None:
    """Fetch transcript text, returning None if unavailable."""
    try:
        segments = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join(seg["text"] for seg in segments)
    except (TranscriptsDisabled, NoTranscriptFound):
        return None
    except Exception as e:
        print(f"  Transcript error for {video_id}: {e}", file=sys.stderr)
        return None


def summarize_transcript(client: anthropic.Anthropic, transcript: str, title: str) -> str:
    """Send transcript to Claude Haiku and return the summary."""
    content = f"Video title: {title}\n\nTranscript:\n{transcript[:15000]}"
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[
            {"role": "user", "content": f"{INTEL_PROMPT}\n\n{content}"}
        ],
    )
    return response.content[0].text


def build_slack_message(summaries: list[dict]) -> str:
    """Format all summaries into a single Slack message."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"*:satellite: AI Daily Brief — {today}*", ""]

    if not summaries:
        lines.append("_No new videos found in the last 24 hours._")
        return "\n".join(lines)

    for item in summaries:
        lines.append(f"*<{item['url']}|{item['title']}>*")
        lines.append(f"_Channel: {item['channel_title']} · {item['published_at'][:10]}_")
        lines.append(item["summary"])
        lines.append("")

    return "\n".join(lines)


def post_to_slack(webhook_url: str, text: str) -> None:
    resp = requests.post(
        webhook_url,
        json={"text": text},
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()


def main():
    channels = load_channels()
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    summaries = []

    for channel in channels:
        channel_id = channel["channel_id"]
        label = channel.get("label", channel_id)
        print(f"Checking channel: {label} ({channel_id})")

        videos = get_recent_videos(youtube, channel_id)
        print(f"  Found {len(videos)} video(s) in last 24h")

        for video in videos:
            print(f"  Processing: {video['title'][:60]}")
            transcript = get_transcript(video["video_id"])
            if transcript is None:
                print("    No transcript available — skipping")
                continue

            summary = summarize_transcript(claude, transcript, video["title"])
            summaries.append({**video, "summary": summary})
            print("    Summarized ✓")

    message = build_slack_message(summaries)
    post_to_slack(SLACK_WEBHOOK_URL, message)
    print(f"\nPosted {len(summaries)} summary/summaries to Slack.")


if __name__ == "__main__":
    main()
