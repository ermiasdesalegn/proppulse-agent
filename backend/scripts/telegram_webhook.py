"""Manage the Telegram webhook.  Usage:  python -m scripts.telegram_webhook info|set|delete

`set` needs PUBLIC_BASE_URL (an https address Telegram can reach, e.g. an ngrok URL) in backend/.env.
For local development you normally do not need a webhook: the app polls Telegram instead.
"""
import asyncio
import sys

from app.config import get_settings
from app.services.telegram import BotApiTelegramService


async def main(action: str) -> None:
    s = get_settings()
    tg = BotApiTelegramService(s.telegram_bot_token)
    me = await tg.get_me()
    print(f"bot: @{me['username']}")
    if action == "set":
        if not s.public_base_url.startswith("https://"):
            sys.exit("PUBLIC_BASE_URL must be an https:// address")
        await tg.set_webhook(f"{s.public_base_url.rstrip('/')}/api/telegram/webhook", s.telegram_webhook_secret)
        print("webhook set")
    elif action == "delete":
        await tg.delete_webhook()
        print("webhook deleted")
    info = await tg.get_webhook_info()
    print({k: info.get(k) for k in ("url", "pending_update_count", "last_error_message")})


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"info", "set", "delete"}:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1]))
