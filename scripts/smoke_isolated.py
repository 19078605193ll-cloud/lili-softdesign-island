"""Real Linux API/Celery smoke and paired backup restore on disposable resources."""

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid
import tarfile
import httpx


def main():
    root = Path(__file__).resolve().parents[1]
    project = "island-smoke-" + uuid.uuid4().hex[:10]
    env = os.environ.copy()
    env["ISLAND_TEST_PASSWORD"] = secrets.token_hex(24)
    for key in [
        "ISLAND_TEST_DB_PORT",
        "ISLAND_TEST_REDIS_PORT",
        "ISLAND_TEST_API_PORT",
    ]:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            env[key] = str(sock.getsockname()[1])
    args = [
        "docker",
        "compose",
        "-f",
        str(root / "compose.test.yml"),
        "-f",
        str(root / "compose.smoke.yml"),
        "-p",
        project,
    ]
    runtime = root / ".test-runtime" / project
    runtime.mkdir(parents=True)

    def run(*parts, **kwargs):
        return subprocess.run([*args, *parts], env=env, cwd=root, check=True, **kwargs)

    origin = "http://127.0.0.1:" + env["ISLAND_TEST_API_PORT"]
    try:
        run("up", "-d", "--wait", "db", "redis")
        run("run", "--rm", "api", "python", "-m", "alembic", "upgrade", "head")
        run("run", "--rm", "api", "python", "-m", "app.knowledge.sync")
        bootstrap = "from argparse import Namespace; from app.core.accounts import execute; from app.core.runtime import run_async; run_async(execute(Namespace(command='create',username='smoke-admin',roles=['administrator']), 'isolated-smoke-password'))"
        run("run", "--rm", "api", "python", "-c", bootstrap)
        run("up", "-d", "api", "worker", "dispatcher")
        with httpx.Client(
            base_url=origin, timeout=15, trust_env=False, headers={"Origin": origin}
        ) as client:
            deadline = time.monotonic() + 45
            while True:
                try:
                    if client.get("/health/ready").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("API readiness timeout")
                time.sleep(0.5)
            token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
            client.headers["X-CSRF-Token"] = token
            response = client.post(
                "/api/v1/auth/login",
                json={"username": "smoke-admin", "password": "isolated-smoke-password"},
            )
            response.raise_for_status()
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            source = "1. 示例题\nA.甲 B.乙 C.丙 D.丁\n答案：A\n解析：示例解析"
            response = client.post(
                "/api/v2/admin/import-batches",
                headers={"Idempotency-Key": uuid.uuid4().hex},
                data={"year": 2025, "period": "first_half", "title": "隔离验收"},
                files={"file": ("paper.md", source.encode())},
            )
            assert response.status_code == 202, response.text
            job = response.json()
            deadline = time.monotonic() + 45
            while job["status"] not in {"succeeded", "failed"}:
                if time.monotonic() > deadline:
                    raise RuntimeError("Worker job timeout")
                time.sleep(0.5)
                job = client.get(job["status_url"]).json()
            assert job["status"] == "succeeded", job
            batch = job["batch_id"]
            base = "/api/v1/admin/markdown-batches/" + batch
            state = client.get(base).json()
            block = state["blocks"][0]
            response = client.post(
                base + "/blocks/" + block["id"] + "/review",
                headers={"If-Match": str(state["revision"])},
                json={
                    "action": "approve",
                    "selections": {"1": {"primary_code": state["topics"][0]["code"]}},
                },
            )
            assert response.status_code == 200, response.text
            response = client.post(base + "/publish")
            assert response.status_code == 200, response.text
            unit = client.get(
                "/api/v2/practice/papers/" + response.json()["paper_id"] + "/questions"
            ).json()["items"][0]
            response = client.post(
                "/api/v2/learning/attempts",
                headers={"Idempotency-Key": uuid.uuid4().hex},
                json={
                    "practice_question_id": unit["id"],
                    "content_version": unit["content_version"],
                    "answers": {unit["parts"][0]["id"]: "A"},
                },
            )
            assert response.status_code == 201, response.text
            assert float(response.json()["score"]) == 1
            assert client.get("/admin/imports").status_code == 200
            if "--browser" in sys.argv:
                print("BROWSER_URL=" + origin, flush=True)
                print("BROWSER_PROJECT=" + project, flush=True)
                (runtime / "browser.json").write_text(
                    json.dumps({"origin": origin, "batch_id": batch}), encoding="utf-8"
                )
                input("Press enter after browser QA to continue restore verification: ")
        run("stop", "api", "dispatcher", "worker")
        with (runtime / "database.dump").open("wb") as stream:
            run(
                "exec",
                "-T",
                "db",
                "pg_dump",
                "-U",
                "island_test",
                "-d",
                "island_isolated_test",
                "-Fc",
                stdout=stream,
            )
        with (runtime / "assets.tar").open("wb") as stream:
            run(
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "api",
                "tar",
                "-C",
                "/srv/island/var/imports",
                "-cf",
                "-",
                ".",
                stdout=stream,
            )
        run("exec", "-T", "db", "createdb", "-U", "island_test", "island_restore_test")
        with (runtime / "database.dump").open("rb") as stream:
            run(
                "exec",
                "-T",
                "db",
                "pg_restore",
                "-U",
                "island_test",
                "-d",
                "island_restore_test",
                "--exit-on-error",
                stdin=stream,
            )
        output = run(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "island_test",
            "-d",
            "island_restore_test",
            "-tAc",
            "SELECT count(*) FROM learning_attempts",
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert output == "1", output
        restored = runtime / "restored-assets"
        restored.mkdir()
        with tarfile.open(runtime / "assets.tar") as archive:
            archive.extractall(restored, filter="data")
        assert any(p.name == "source.md" for p in restored.rglob("source.md"))
        manifest = {
            "project": project,
            "linux_api_worker_flow": "passed",
            "restored_attempts": int(output),
            "restored_source_files": len(list(restored.rglob("source.md"))),
            "production_data_used": False,
        }
        (runtime / "result.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        print(json.dumps(manifest), flush=True)
    finally:
        run("down", "--volumes")


if __name__ == "__main__":
    main()
