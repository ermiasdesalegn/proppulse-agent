import asyncio
import sys

# psycopg's async driver cannot run on Windows' default Proactor event loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
