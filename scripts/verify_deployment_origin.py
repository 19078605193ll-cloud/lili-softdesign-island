"""Read-only check of Compose and running website/voice origin configuration."""

from __future__ import annotations

import argparse
import json
import subprocess


def allowed_origins(value: str) -> list[str]:
    if value.strip().startswith("["):
        return json.loads(value)
    return [item.strip() for item in value.split(",") if item.strip()]


def check_origins(expected: str, api: dict[str, str], voice: dict[str, str]) -> None:
    if not expected or expected.endswith("/"):
        raise ValueError("Compose PUBLIC_ORIGIN must be an explicit origin without a trailing slash")
    if api.get("PUBLIC_ORIGIN") != expected:
        raise ValueError("Running API PUBLIC_ORIGIN differs from Compose; recreate the API container")
    if allowed_origins(voice.get("ALLOWED_ORIGINS", "")) != [expected]:
        raise ValueError("Running voice ALLOWED_ORIGINS differs from PUBLIC_ORIGIN; recreate voice-input")
    if expected.startswith("https://") and api.get("ALLOW_INSECURE_HTTP", "false").lower() != "false":
        raise ValueError("HTTPS deployment must set ALLOW_INSECURE_HTTP=false")


def run_json(command: list[str]):
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        # Do not expose Compose/provider configuration in failure output.
        raise RuntimeError(f"Read-only Docker check failed (exit {result.returncode})")
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="softdesign-island")
    parser.add_argument("--compose-file", default="compose.http.yml")
    parser.add_argument("--env-file", action="append")
    args = parser.parse_args()
    compose = ["docker", "compose"]
    for path in args.env_file or ["deploy/production.env", "deploy/compose.env"]:
        compose.extend(["--env-file", path])
    compose.extend(["-f", args.compose_file, "-p", args.project])
    try:
        config = run_json([*compose, "config", "--format", "json"])
        desired = config["services"]["api"]["environment"].get("PUBLIC_ORIGIN", "")
        voice_desired = config["services"]["voice-input"]["environment"]
        if allowed_origins(voice_desired.get("ALLOWED_ORIGINS", "")) != [desired]:
            raise ValueError("Compose website and voice origins differ")
        ids = {}
        for service in ("api", "voice-input"):
            result = subprocess.run([*compose, "ps", "-q", service], capture_output=True, text=True, timeout=30)
            values = result.stdout.split()
            if result.returncode or len(values) != 1:
                raise ValueError(f"Expected exactly one running {service} container")
            ids[service] = values[0]
        containers = run_json(["docker", "inspect", ids["api"], ids["voice-input"]])
        environments = [dict(item.split("=", 1) for item in row["Config"]["Env"]) for row in containers]
        check_origins(desired, environments[0], environments[1])
        service_url = environments[0].get("VOICE_SERVICE_URL", "").rstrip("/")
        if not service_url:
            raise ValueError("API VOICE_SERVICE_URL is missing")
        probe = (
            "import json,urllib.request; "
            f"base={service_url!r}; origin={desired!r}; "
            "ready=urllib.request.urlopen(base+'/health/ready',timeout=8); "
            "request=urllib.request.Request(base+'/v1/anonymous-tokens',method='OPTIONS',"
            "headers={'Origin':origin,'Access-Control-Request-Method':'POST'}); "
            "response=urllib.request.urlopen(request,timeout=8); "
            "print(json.dumps({'ready':ready.status,'origin_check':response.status}))"
        )
        result = run_json(["docker", "exec", ids["api"], "python", "-c", probe])
        if result != {"ready": 200, "origin_check": 200}:
            raise ValueError("Voice readiness or origin probe failed")
        print(json.dumps({"status": "ok", "public_origin": desired, **result}))
        return 0
    except (ValueError, KeyError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Deployment origin verification failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
