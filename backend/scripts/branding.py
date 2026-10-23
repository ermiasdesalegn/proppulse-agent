"""Give the Telegram bot and channel the PropPulse look.  Usage:  python -m scripts.branding [--photo docs/brand/logo-640.png]

Sets the bot's name, short description and "What can this bot do?" text, and the channel's profile photo (the bot must be an admin of the
channel with the "Change channel info" right). Telegram's API cannot change the bot's own profile picture: send the same PNG to
@BotFather with /setuserpic.
"""
import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from app.config import get_settings

NAME = "PropPulse AI"
SHORT = "Your Addis Ababa property assistant: find homes, compare developers, book site visits."
ABOUT = ("PropPulse AI helps you find apartments and homes across Addis Ababa. Tell me your budget, bedrooms and area in English or Amharic, "
         "and I will compare options from different developers, explain payment plans and book a site visit for you.")


async def call(client: httpx.AsyncClient, token: str, method: str, **data) -> dict:
    res = await client.post(f"https://api.telegram.org/bot{token}/{method}", data=data)
    body = res.json()
    print(f"{method}: {'ok' if body.get('ok') else body.get('description')}")
    return body


async def main(photo_name: str, photo_bytes: bytes) -> int:
    s = get_settings()
    if not s.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not set")
        return 1
    async with httpx.AsyncClient(timeout=60) as client:
        await call(client, s.telegram_bot_token, "setMyName", name=NAME)
        await call(client, s.telegram_bot_token, "setMyShortDescription", short_description=SHORT)
        await call(client, s.telegram_bot_token, "setMyDescription", description=ABOUT)
        if s.telegram_channel_id and photo_bytes:
            res = await client.post(f"https://api.telegram.org/bot{s.telegram_bot_token}/setChatPhoto", data={"chat_id": s.telegram_channel_id},
                                    files={"photo": (photo_name, photo_bytes, "image/png")})
            body = res.json()
            print(f"setChatPhoto: {'ok' if body.get('ok') else body.get('description')}")
        else:
            print("channel photo skipped (no TELEGRAM_CHANNEL_ID or no photo file)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--photo", type=Path, default=Path(__file__).resolve().parents[2] / "docs" / "brand" / "logo-640.png")
    photo = p.parse_args().photo
    sys.exit(asyncio.run(main(photo.name, photo.read_bytes() if photo.exists() else b"")))
