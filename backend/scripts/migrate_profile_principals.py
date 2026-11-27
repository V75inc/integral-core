"""Migrate one workspace after operator review; defaults to read-only inspection.

Run from backend: .venv/bin/python -m scripts.migrate_profile_principals
  --workspace-id ID --actor-user-id ID [--apply]
"""

import argparse
import asyncio
import json


async def run(args):
    from app.agentive.services.profile_management import migrate_published_principals
    from app.main import app  # noqa: F401 -- initialize the configured datastore

    result = await migrate_published_principals(
        args.actor_user_id, args.workspace_id, apply=args.apply
    )
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--actor-user-id", required=True)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(run(parser.parse_args()))
