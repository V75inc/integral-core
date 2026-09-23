#!/usr/bin/env python3
"""Decide whether this workflow should publish backend/pyproject.toml.

Writes publish, owns, already, and version to $GITHUB_OUTPUT.

RELEASE_INDEX=testpypi publishes only PEP 440 pre-releases.
RELEASE_INDEX=pypi publishes only final versions.
A version already present on that index is not published again.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
import urllib.error
import urllib.request
from packaging.version import Version

INDEX_URLS = {
    "testpypi": "https://test.pypi.org/pypi/integral-core/json",
    "pypi": "https://pypi.org/pypi/integral-core/json",
}


def _fail(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)
    raise SystemExit(1)


def _already_published(index: str, version: str) -> bool:
    url = INDEX_URLS[index]
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            releases = json.load(resp).get("releases") or {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        _fail(f"could not read {url}: HTTP {exc.code}")
    except urllib.error.URLError as exc:
        _fail(f"could not read {url}: {exc.reason}")
    files = releases.get(version) or []
    return bool(files)


def main() -> None:
    index = os.environ["RELEASE_INDEX"]
    if index not in INDEX_URLS:
        _fail(f"unknown RELEASE_INDEX {index!r}")
    event = os.environ["EVENT_NAME"]
    ref_name = os.environ["REF_NAME"]
    dispatch_tag = (os.environ.get("DISPATCH_TAG") or "").strip()

    root = os.environ.get("GITHUB_WORKSPACE") or "."
    pyproject = os.path.join(root, "backend", "pyproject.toml")
    with open(pyproject, "rb") as fh:
        version = tomllib.load(fh)["project"]["version"]

    if event == "workflow_dispatch":
        if dispatch_tag.removeprefix("v") != version:
            _fail(f"dispatch tag {dispatch_tag} does not match package version {version}")
    elif ref_name != "main":
        if ref_name.removeprefix("v") != version:
            _fail(f"tag {ref_name} does not match package version {version}")

    pre = Version(version).is_prerelease
    owns = (index == "testpypi" and pre) or (index == "pypi" and not pre)
    already = _already_published(index, version) if owns else False
    publish = owns and not already

    if not owns:
        other = "PyPI" if index == "testpypi" else "TestPyPI"
        print(f"::notice::{version} is not for this index; {other} owns it")
    elif already:
        print(f"::notice::{version} is already on {index}")
    else:
        print(f"publishing {version} to {index}")

    out_path = os.environ["GITHUB_OUTPUT"]
    with open(out_path, "a", encoding="utf-8") as fh:
        fh.write(f"publish={'true' if publish else 'false'}\n")
        fh.write(f"owns={'true' if owns else 'false'}\n")
        fh.write(f"already={'true' if already else 'false'}\n")
        fh.write(f"version={version}\n")


if __name__ == "__main__":
    main()
