"""Software designer practice backend."""

import asyncio
import sys

# Psycopg's async implementation requires a selector-based loop on Windows.
# Set the policy before Alembic, pytest, or the ASGI server creates an event loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
