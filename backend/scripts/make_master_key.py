"""Print a new master key for SECRETS_MASTER_KEY.

    python -m scripts.make_master_key

The key encrypts each agency's stored credentials (its Telegram bot token, its calendar key). Put it in the server's environment
(Render, a secrets manager), never in the database or in version control. Losing it makes the stored credentials unreadable, so
agencies would have to connect their channels again; changing it has the same effect. Keep a copy somewhere safe.
"""
from app.vault import generate_master_key

if __name__ == "__main__":
    print(generate_master_key())
