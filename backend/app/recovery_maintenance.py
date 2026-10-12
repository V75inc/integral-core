"""Database-only maintenance: no API, harness, scheduler or public listener."""

import asyncio
import json
import sys
from pathlib import Path

from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database


async def run(home: Path, command: str, arguments: dict) -> dict:
    """Execute one offline maintenance command against the managed prime store."""
    from app.local_runtime import read_json
    from app.services import access_recovery

    config = read_json(home / "installation.json")
    database = create_database(db_type="postgres", min_size=1, max_size=1)
    try:
        async with scoped_default_context_async(GraphContext(database=database)):
            directory = (
                Path(arguments["checkpoint_directory"])
                if arguments.get("checkpoint_directory")
                else None
            )
            if command == "checkpoint":
                return await access_recovery.checkpoint(
                    installation_id=config["installation_id"],
                    directory=directory,
                    home=home,
                )
            if command == "recovery-review":
                return await access_recovery.review(
                    installation_id=config["installation_id"], directory=directory
                )
            if command == "recovery-apply":
                return await access_recovery.apply(
                    installation_id=config["installation_id"],
                    directory=directory,
                    expected_digest=arguments["review_digest"],
                    owner_ids=arguments.get("owner_ids", []),
                    password_hash=arguments.get("password_hash", ""),
                )
            raise ValueError("Unknown recovery maintenance command")
    finally:
        await database.close()


if __name__ == "__main__":
    print(
        json.dumps(
            asyncio.run(run(Path(sys.argv[1]), sys.argv[2], json.load(sys.stdin)))
        )
    )
