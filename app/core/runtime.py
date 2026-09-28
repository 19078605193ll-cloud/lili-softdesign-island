"""The same async loop policy for Windows CLI, migrations and workers."""

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def run_async(coroutine: Coroutine[Any, Any, T]) -> T:
    factory = (
        asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    )
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(coroutine)
