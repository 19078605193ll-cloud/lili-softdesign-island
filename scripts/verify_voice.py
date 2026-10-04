"""Exercise the real voice container with mock providers and disposable host data."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    name = "island-voice-test-" + uuid.uuid4().hex[:10]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--publish",
            f"127.0.0.1:{port}:9100",
            "--env",
            "MOCK_PROVIDERS_ENABLED=true",
            "--env",
            "POLISH_ENABLED=true",
            "--env",
            "ANONYMOUS_TOKENS_ENABLED=true",
            "--env",
            "ANONYMOUS_TOKEN_TTL_SECONDS=900",
            "--env",
            "ANONYMOUS_TOKEN_SECRET=isolated-voice-fixture-key-only-32-bytes",
            "--env",
            "ALLOWED_ORIGINS=http://test",
            "softdesign-island-voice:0.1.0",
        ],
        check=True,
        cwd=root,
        stdout=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                with urllib.request.urlopen(
                    url + "/health/ready", timeout=1
                ) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Mock voice container did not become ready")
        env = os.environ.copy()
        env["VOICE_TEST_URL"] = url
        return subprocess.run(
            [
                sys.executable,
                "scripts/test_isolated.py",
                "tests/test_voice_service.py",
                "-q",
                "--tb=short",
            ],
            cwd=root,
            env=env,
            check=False,
        ).returncode
    finally:
        subprocess.run(["docker", "stop", name], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())
