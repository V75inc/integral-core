"""Write (or check) docs/generated/capability-map.{json,md}.

    python scripts/generate_capability_map.py            # regenerate
    python scripts/generate_capability_map.py --check    # exit 1 when stale
    python scripts/generate_capability_map.py --package-root ../other/apps \
        --stdout                                          # report on external Apps

External package roots (for example an HR App kept outside Core) are reported
to stdout only; the committed map covers the public fixtures under examples/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.capability_map import (  # noqa: E402
    DEFAULT_FIXTURE_ROOTS,
    MAP_JSON_PATH,
    MAP_MD_PATH,
    build_capability_map,
    render_json,
    render_markdown,
)


def main() -> int:
    """Regenerate, check, or print the capability map."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--package-root", action="append", type=Path, default=[])
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    roots = [*DEFAULT_FIXTURE_ROOTS, *args.package_root]
    cap_map = build_capability_map(roots)
    outputs = {
        MAP_JSON_PATH: render_json(cap_map),
        MAP_MD_PATH: render_markdown(cap_map),
    }

    if args.stdout or args.package_root:
        sys.stdout.write(outputs[MAP_MD_PATH])
        return 0
    if args.check:
        stale = [
            p
            for p, text in outputs.items()
            if not p.is_file() or p.read_text(encoding="utf-8") != text
        ]
        for path in stale:
            print(f"stale: {path} — run backend/scripts/generate_capability_map.py")
        return 1 if stale else 0
    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
