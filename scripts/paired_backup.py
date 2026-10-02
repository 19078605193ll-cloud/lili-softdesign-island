"""Create a paired backup after stopping only the named deployment's writers.

No restore operation is exposed here. Destination must be a new directory.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, default=Path("compose.production.yml"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(args.env_file.resolve()),
        "-f",
        str(root / args.compose_file),
        "-p",
        args.project,
    ]

    def run(*parts, **kw):
        return subprocess.run([*compose, *parts], check=True, **kw)

    run("stop", "api", "dispatcher", "worker")
    try:
        with (destination / "database.dump").open("wb") as stream:
            run(
                "exec",
                "-T",
                "db",
                "pg_dump",
                "-U",
                "island_migrator",
                "-d",
                "island",
                "-Fc",
                stdout=stream,
            )
        with (destination / "assets.tar").open("wb") as stream:
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
        files = {
            p.name: hashlib.file_digest(p.open("rb"), "sha256").hexdigest()
            for p in destination.iterdir()
            if p.is_file()
        }
        (destination / "manifest.json").write_text(
            json.dumps(
                {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "project": args.project,
                    "files": files,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        run("start", "api", "worker", "dispatcher")


if __name__ == "__main__":
    main()
