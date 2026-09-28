from __future__ import annotations

import argparse
import asyncio

import uvicorn
from app.config import get_settings


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Return the event loop required by psycopg async on Windows."""
    return asyncio.SelectorEventLoop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the software designer API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        loop=selector_loop_factory,
        proxy_headers=True,
        # Production API has no published port; Caddy is the trusted ingress.
        forwarded_allow_ips="*" if get_settings().app_env == "production" else "127.0.0.1",
    )


if __name__ == "__main__":
    main()
