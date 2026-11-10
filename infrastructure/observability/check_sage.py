"""Check resolved Compose ingestion settings without printing credentials."""

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def validate(settings):
    errors = []
    try:
        endpoint = urlsplit(settings.get("SAGE_REMOTE_WRITE_ENDPOINT", ""))
    except ValueError:
        endpoint = urlsplit("")
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        errors.append("SAGE_REMOTE_WRITE_ENDPOINT must be the approved HTTPS ingestion URL")
    if not re.fullmatch(r"JWT [^\s<>]+", settings.get("SAGE_REMOTE_WRITE_AUTHORIZATION", "")):
        errors.append("SAGE_REMOTE_WRITE_AUTHORIZATION must contain JWT <ingestion-token>")
    for key in ("SAGE_GROUP", "SAGE_SYSTEM", "SAGE_ENV", "SAGE_DC"):
        value = settings.get(key, "")
        if not value.strip() or value != value.strip() or any(c in value for c in "<>\r\n"):
            errors.append(f"{key} must be set to the real authorized value")
    try:
        agent = urlsplit(settings.get("SAGE_AGENT_OTLP_ENDPOINT", ""))
    except ValueError:
        agent = urlsplit("")
    if agent.scheme not in {"http", "https"} or not agent.hostname:
        errors.append("SAGE_AGENT_OTLP_ENDPOINT must identify the host agent OTLP listener")
    return errors


def main():
    result = subprocess.run(
        ["docker", "compose", "--profile", "sage", "config", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit("Cannot resolve Compose configuration; run docker compose config --quiet")
    settings = json.loads(result.stdout)["services"]["otel-collector"]["environment"]
    errors = validate(settings)
    if errors:
        raise SystemExit("Sage forwarding is not configured:\n- " + "\n- ".join(errors))
    print("Sage settings are present. Group authorization and live ingestion still need verification.")


if __name__ == "__main__":
    main()
