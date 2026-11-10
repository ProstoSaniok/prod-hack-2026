import argparse
import json
import sys

from .client import Client, SageError
from .config import load
from .reconcile import apply, plan


def main():
    parser = argparse.ArgumentParser(
        description="Additive Sage IaC for hack-led-03; no delete operations"
    )
    parser.add_argument("command", choices=("validate", "plan", "apply"))
    parser.add_argument("--manifest", default="objects/manifest.json")
    parser.add_argument("--yes", action="store_true", help="Authorize writes for apply")
    parser.add_argument(
        "--allow-update",
        action="store_true",
        help="Explicitly authorize updating existing named objects",
    )
    args = parser.parse_args()
    try:
        resources = load(args.manifest)
        if args.command == "validate":
            print(json.dumps({"valid": True, "resources": [r.key for r in resources]}))
            return 0
        if args.command == "apply" and not args.yes:
            raise SageError("apply requires --yes; review plan first")
        client = Client.from_environment()
        changes = plan(client, resources)
        print(
            json.dumps(
                [{"id": c.resource.key, "action": c.action} for c in changes], indent=2
            )
        )
        if args.command == "apply":
            apply(client, changes, allow_update=args.allow_update)
            print("Applied and verified. Re-run plan to check convergence.")
        return 0
    except (SageError, OSError, ValueError, KeyError, TypeError) as error:
        # Untrusted API/config values must not leak via exception representations.
        message = (
            str(error)
            if isinstance(error, SageError)
            else f"Invalid configuration, response, or local file ({type(error).__name__})"
        )
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
