"""Migrate one workspace after operator review; defaults to read-only inspection.

Run from backend: .venv/bin/python -m scripts.migrate_workspace_email
  --workspace-id ID --actor-user-id ID [--apply]
"""

import argparse
import asyncio
import json


async def run(args):
    from app.services.email_migration import migrate_workspace

    result = await migrate_workspace(
        args.actor_user_id, args.workspace_id, apply=args.apply
    )
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--actor-user-id", required=True)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(run(parser.parse_args()))
