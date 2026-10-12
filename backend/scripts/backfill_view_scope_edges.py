"""Report legacy View scope; --apply wires only proven unique Track paths."""

import argparse
import asyncio
import json

from app.services.view_scope_migration import migrate_view_scopes


async def main(apply: bool) -> None:
    from app.services.db_init import init_prime_db

    init_prime_db()
    print(json.dumps(await migrate_view_scopes(apply=apply), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply proven unique scopes; requires graph transactions",
    )
    args = parser.parse_args()
    asyncio.run(main(args.apply))
