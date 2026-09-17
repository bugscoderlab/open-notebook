"""CLI for team-access admin operations.

Usage:
    uv run python -m open_notebook.admin bootstrap --email a@b.c --password …
    uv run python -m open_notebook.admin dev-seed   # DEV ONLY
"""

import argparse
import asyncio
import sys

from dotenv import load_dotenv

# SurrealDB credentials live in .env (same source the API uses); the CLI is
# otherwise standalone. Must run before any DB access, not before imports —
# db_connection reads the environment at call time.
load_dotenv()

from open_notebook.admin import BootstrapError, bootstrap, dev_seed

DEV_SEED_WARNING = (
    "dev-seed is DEV ONLY — it creates/reset accounts with the shared "
    "password 'password'. NEVER run this against a production database."
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="open_notebook.admin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    boot = sub.add_parser(
        "bootstrap",
        help="Seed org + HR/Finance/Executive teams + the first admin "
        "(Executive). Refuses to run when any user exists.",
    )
    boot.add_argument("--email", required=True, help="first admin's email")
    boot.add_argument("--password", required=True, help="first admin's password")
    boot.add_argument("--display-name", default="Admin", help="first admin's display name")

    sub.add_parser(
        "dev-seed",
        help=f"Upsert the four UAT personas (password: 'password'). {DEV_SEED_WARNING}",
    )

    args = parser.parse_args()

    if args.command == "bootstrap":
        try:
            result = asyncio.run(
                bootstrap(args.email, args.password, args.display_name)
            )
        except BootstrapError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"bootstrapped organization {result['organization_id']}")
        print(f"teams: {result['teams']}")
        print(f"first admin: {result['admin_id']} <{args.email.strip().lower()}>")
    elif args.command == "dev-seed":
        print(f"WARNING: {DEV_SEED_WARNING}", file=sys.stderr)
        try:
            messages = asyncio.run(dev_seed())
        except BootstrapError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        for message in messages:
            print(message)


if __name__ == "__main__":
    main()
