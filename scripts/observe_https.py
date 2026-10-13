"""Observe public HTTPS readiness and certificate expiry without logging user data."""
from __future__ import annotations

import argparse
import json
import socket
import ssl
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def sample(origin: str) -> dict:
    row = {"at": datetime.now(timezone.utc).isoformat()}
    hostname = urlsplit(origin).hostname
    try:
        row["addresses"] = sorted({entry[4][0] for entry in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)})
        with socket.create_connection((hostname, 443), timeout=8) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=hostname) as connection:
                certificate = connection.getpeercert()
        row["certificate_days_remaining"] = round((ssl.cert_time_to_seconds(certificate["notAfter"]) - time.time()) / 86400, 1)
        if row["certificate_days_remaining"] < 21:
            raise ValueError("Certificate expires in less than 21 days")
        started = time.monotonic()
        request = urllib.request.Request(origin + "/health/ready", headers={
            "User-Agent": "softdesign-island-health/1.0", "Accept": "application/json",
        })
        with urllib.request.urlopen(request, timeout=8) as response:
            row["status"] = response.status
            response.read()
        row["health_ms"] = round((time.monotonic() - started) * 1000, 1)
        row["ok"] = row["status"] == 200
    except urllib.error.HTTPError as exc:
        row.update(ok=False, error=type(exc).__name__, status=exc.code)
    except Exception as exc:
        row.update(ok=False, error=type(exc).__name__)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://app.liuli2026.top")
    parser.add_argument("--duration", type=int, default=0)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    parsed = urlsplit(args.origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
        parser.error("--origin must be an HTTPS origin without credentials or path")
    if args.duration < 0 or args.interval < 1:
        parser.error("duration must be nonnegative and interval must be positive")
    deadline = time.monotonic() + args.duration
    failures = 0
    while True:
        row = sample(args.origin)
        failures += not row["ok"]
        line = json.dumps(row)
        print(line, flush=True)
        if args.report:
            with args.report.open("a", encoding="utf-8") as report:
                report.write(line + "\n")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(args.interval, remaining))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
