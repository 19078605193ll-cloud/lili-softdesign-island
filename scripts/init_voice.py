"""Create a persistent signing key without printing it or touching provider keys."""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path


def initialize(target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8") as handle:
            handle.write("ANONYMOUS_TOKEN_SECRET=" + secrets.token_urlsafe(48) + "\n")
    except FileExistsError:
        contents = target.read_text(encoding="utf-8")
        values = [
            line.partition("=")[2].strip()
            for line in contents.splitlines()
            if line.startswith("ANONYMOUS_TOKEN_SECRET=")
        ]
        if len(values) != 1 or len(values[0].encode("utf-8")) < 32:
            raise ValueError(
                "Existing voice.env has an invalid signing key; repair it explicitly"
            )
        return False
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".local-runtime/voice.env",
    )
    arguments = parser.parse_args()
    created = initialize(arguments.output)
    print(
        "Voice signing configuration created."
        if created
        else "Existing voice signing configuration preserved."
    )
