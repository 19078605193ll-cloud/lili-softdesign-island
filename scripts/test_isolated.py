"""Run tests against newly created disposable containers, never the local .env."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import uuid
import socket


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    project = "island-test-" + uuid.uuid4().hex[:12]
    runtime = root / ".test-runtime" / project
    runtime.mkdir(parents=True)
    env = os.environ.copy()
    env.update(ISLAND_TEST_PASSWORD=secrets.token_hex(24), APP_ENV="test")
    for key in ("ISLAND_TEST_DB_PORT", "ISLAND_TEST_REDIS_PORT"):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            env[key] = str(sock.getsockname()[1])
    compose = ["docker", "compose", "-f", str(root / "compose.test.yml"), "-p", project]

    def run(*args: str, **kwargs):
        return subprocess.run(
            [*compose, *args], env=env, cwd=root, check=True, **kwargs
        )

    try:
        run("up", "-d", "--wait", "--wait-timeout", "60")
        db_port = env["ISLAND_TEST_DB_PORT"]
        redis_port = env["ISLAND_TEST_REDIS_PORT"]
        env.update(
            DATABASE_URL=f"postgresql+psycopg://island_test:{env['ISLAND_TEST_PASSWORD']}@127.0.0.1:{db_port}/island_isolated_test?connect_timeout=5",
            REDIS_URL=f"redis://127.0.0.1:{redis_port}/0",
            CELERY_BROKER_URL=f"redis://127.0.0.1:{redis_port}/1",
            IMPORT_STORAGE_ROOT=str(runtime / "assets"),
            PUBLIC_ORIGIN="http://test",
            AI_API_KEY="",
            AI_BASE_URL="",
            AI_CLASSIFICATION_MODEL="",
            AI_TEXT_MODEL="",
            ISLAND_TEST_RUN=project,
        )
        env["TEST_DATABASE_URL"] = env["DATABASE_URL"]
        # A database-side marker proves that the selected database belongs to this run.
        run(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "island_test",
            "-d",
            "island_isolated_test",
            "-c",
            f"CREATE SCHEMA test_guard; CREATE TABLE test_guard.identity (run_id text PRIMARY KEY); INSERT INTO test_guard.identity VALUES ('{project}');",
        )
        args = sys.argv[1:] or ["-q"]
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "pytest", *args], env=env, cwd=root
        )
        (runtime / "result.json").write_text(
            json.dumps({"project": project, "exit_code": result.returncode}),
            encoding="utf-8",
        )
        return result.returncode
    finally:
        run("down", "--volumes")


if __name__ == "__main__":
    raise SystemExit(main())
