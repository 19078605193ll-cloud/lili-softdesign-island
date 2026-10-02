import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path
import pytest
from httpx import AsyncClient
from tests.test_h5 import setup_questions
from tests.test_markdown_integration import storage as storage

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("H5_E2E") != "1",
    reason="Set H5_E2E=1 through scripts/test_isolated.py to run browser acceptance",
)
async def test_browser_learning_flow(client, session, storage, tmp_path):
    await setup_questions(client, session)
    from app.models import User, UserRole
    from app.core.security import passwords
    for browser in ("chromium", "webkit"):
        session.add(User(username="h5-" + browser, password_hash=passwords.hash("isolated-test-password")))
        session.add(User(username="profile-" + browser, password_hash=passwords.hash("isolated-test-password")))
        admin = User(username="users-admin-" + browser, password_hash=passwords.hash("isolated-test-password"))
        session.add(admin)
        await session.flush()
        session.add(UserRole(user_id=admin.id, role="administrator"))
    await session.commit()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update(
        H5_E2E_PORT=str(port),
        H5_BASE_URL=f"http://127.0.0.1:{port}",
        PUBLIC_ORIGIN=f"http://127.0.0.1:{port}",
        AI_API_KEY="isolated-fixture-only",
        AI_TUTOR_MODEL="isolated-fixture-only",
    )
    root = Path(__file__).resolve().parents[1]
    log = tmp_path / "browser-api.log"
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.h5_browser_server"],
            cwd=root,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            async with AsyncClient() as http:
                for _ in range(100):
                    try:
                        r = await http.get(env["H5_BASE_URL"] + "/health/live")
                        if r.status_code == 200:
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail(log.read_text(encoding="utf-8"))
            executable = "npm.cmd" if sys.platform == "win32" else "npm"
            result = await asyncio.to_thread(
                subprocess.run,
                [executable, "run", "test:e2e"],
                cwd=root / "web/h5",
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
            )
            assert result.returncode == 0, (
                result.stdout
                + "\n"
                + result.stderr
                + "\n"
                + log.read_text(encoding="utf-8")[-4000:]
            )
        finally:
            process.terminate()
            await asyncio.to_thread(process.wait, timeout=15)
