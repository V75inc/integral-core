"""Every annotation must be evaluable where CI evaluates it.

CI pins **Python 3.11**; every venv in this repo is **3.14**. Under PEP 649,
3.14 defers annotation evaluation, so a signature naming something the module
only imports *inside* a function body is fine locally and fails on CI — at
collection, before any test runs.

That is not hypothetical. PR #44's first CI run was red with::

    ERROR collecting tests/test_workspace_init.py
    NameError: name 'Workspace' is not defined

from a helper annotated ``-> Workspace`` in a module that imports `app.*`
inside its test bodies. Local `pytest` was green, and so was `make verify-ci`:
it reproduces CI's flags (TESTING=1, no .env, smoke marker, xdist) but runs
whatever interpreter the venv has. Interpreter version is a fifth axis of
CI/local divergence, and nothing covered it.

This forces evaluation, so 3.14 fails the same way 3.11 would. On 3.11 it is
near-redundant — the import itself would already have raised — which is the
point: the check belongs where the gap is.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path
from typing import Any, Dict, List

TESTS_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = TESTS_ROOT.parent


def _module_names() -> List[str]:
    """Every test module, plus whatever of `app.*` is already imported.

    Test modules are the exposure that matters: pytest imports them at
    collection on CI, so a bad annotation there is a red run rather than a
    failed test. `app.*` modules are checked only if something already
    imported them — walking and importing the whole package here would trade a
    cheap check for import side effects.
    """
    names: List[str] = []
    for mod in pkgutil.walk_packages([str(TESTS_ROOT)], prefix="tests."):
        if mod.name.rsplit(".", 1)[-1].startswith("test_"):
            names.append(mod.name)
    names.extend(n for n in list(sys.modules) if n.startswith("app."))
    return names


def _force_annotations(module: Any, problems: List[str]) -> None:
    """Evaluate exactly what 3.11 evaluates at `def` time — no more.

    Reading ``__annotations__`` is the right probe. Under PEP 649 that access
    is what triggers evaluation on 3.14, so an UNQUOTED annotation resolves
    here and raises just as 3.11 would at definition time. A QUOTED annotation
    (``"StagedChange"``) stays a string on every version and is left alone —
    quoting is the documented escape hatch, and ``from __future__ import
    annotations`` quotes everything.

    ``typing.get_type_hints()`` is the wrong probe: it also resolves those
    strings, so it flags the deliberate TYPE_CHECKING pattern CI is perfectly
    happy with. The first version of this guard did exactly that and reported
    thirteen false findings across ``app/``.
    """
    for name, obj in vars(module).items():
        if getattr(obj, "__module__", None) != module.__name__:
            continue
        if not (callable(obj) or isinstance(obj, type)):
            continue
        try:
            getattr(obj, "__annotations__", None)
        except Exception as exc:  # noqa: BLE001 — the whole point is to report
            problems.append(f"{module.__name__}.{name}: {type(exc).__name__}: {exc}")


def test_annotations_resolve_the_way_ci_resolves_them() -> None:
    sys.path.insert(0, str(BACKEND_ROOT))
    problems: List[str] = []
    seen: Dict[str, bool] = {}

    for name in _module_names():
        if name in seen:
            continue
        seen[name] = True
        module = sys.modules.get(name)
        if module is None:
            try:
                module = importlib.import_module(name)
            except BaseException as exc:  # noqa: BLE001
                # `pytest.skip(allow_module_level=True)` raises `Skipped`, a
                # BaseException — a deliberately skipped module is not a
                # finding. A genuine import failure is, and CI would hit it
                # too.
                if type(exc).__name__ == "Skipped":
                    continue
                problems.append(f"{name}: import failed: {exc!r}")
                continue
        _force_annotations(module, problems)

    assert not problems, (
        "Annotations that Python 3.11 (CI) evaluates at definition time but "
        "3.14 (local) defers. Import the name at module scope, or quote the "
        "annotation:\n  " + "\n  ".join(problems)
    )
