"""Explicit bootstrap for one reviewed installed managed-channel distribution.

No call site is wired into server startup. Distribution metadata and RECORD
ownership checks constrain selection; they do not authenticate package bytes.
Only trusted, preinstalled code from an independently verified artifact can
be admitted. This is not a sandbox or an installer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import metadata, util
from inspect import iscoroutinefunction
from pathlib import Path

from app.connectors.channel_packages import ChannelPackage, ChannelPackageRegistry

ENTRY_POINT_GROUP = "integral.managed_channels.v1"
_DIST_NAME = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9._-]*[a-zA-Z0-9])?")
_SLUG = re.compile(r"[a-z][a-z0-9_]{0,63}")
_PINNED_VERSION = re.compile(r"[0-9][A-Za-z0-9.!+_-]*")
# V1 limits the target to a single top-level module, so checking its import
# origin cannot implicitly execute an unreviewed parent package.
_FACTORY = re.compile(r"([A-Za-z_][A-Za-z0-9_]*):([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class ApprovedChannelDistribution:
    """A trusted operator review of one exact distribution and entry point."""

    distribution: str
    version: str
    slug: str
    factory: str


class ChannelPackageDiscoveryError(RuntimeError):
    """The explicit distribution was not safe to register."""


def _normalized_name(value: str) -> str:
    if not isinstance(value, str) or not _DIST_NAME.fullmatch(value):
        raise ChannelPackageDiscoveryError("Invalid distribution identity")
    return re.sub(r"[-_.]+", "-", value).lower()


def _approved_entry(approval: ApprovedChannelDistribution) -> metadata.EntryPoint:
    if (
        not isinstance(approval, ApprovedChannelDistribution)
        or not isinstance(approval.version, str)
        or not _PINNED_VERSION.fullmatch(approval.version)
        or not isinstance(approval.slug, str)
        or not _SLUG.fullmatch(approval.slug)
        or not isinstance(approval.factory, str)
    ):
        raise ChannelPackageDiscoveryError("An exact channel approval is required")
    target = _FACTORY.fullmatch(approval.factory)
    if not target:
        raise ChannelPackageDiscoveryError("Invalid channel factory target")
    name = _normalized_name(approval.distribution)
    found = tuple(metadata.distributions(name=name))
    if len(found) != 1:
        raise ChannelPackageDiscoveryError(
            "Approved distribution is missing or ambiguous"
        )
    (distribution,) = found
    if (
        _normalized_name(distribution.metadata["Name"]) != name
        or distribution.version != approval.version
    ):
        raise ChannelPackageDiscoveryError(
            "Approved distribution identity does not match"
        )
    points = [
        entry for entry in distribution.entry_points if entry.group == ENTRY_POINT_GROUP
    ]
    if len(points) != 1:
        raise ChannelPackageDiscoveryError(
            "The package must declare one managed channel"
        )
    (entry,) = points
    if entry.name != approval.slug or entry.value != approval.factory:
        raise ChannelPackageDiscoveryError(
            "Approved channel entry point does not match"
        )

    module = target[1]
    files = {str(path) for path in distribution.files or ()}
    candidates = {f"{module}.py", f"{module}/__init__.py"} & files
    if len(candidates) != 1:
        raise ChannelPackageDiscoveryError("Cannot establish package factory ownership")
    (relative,) = candidates
    root = Path(distribution.locate_file("")).resolve(strict=True)
    source = Path(distribution.locate_file(relative)).resolve(strict=True)
    if not source.is_relative_to(root) or not source.is_file():
        raise ChannelPackageDiscoveryError("Cannot establish package factory ownership")
    spec = util.find_spec(module)
    if (
        spec is None
        or spec.origin is None
        or Path(spec.origin).resolve(strict=True) != source
    ):
        raise ChannelPackageDiscoveryError(
            "Approved factory is shadowed or unavailable"
        )
    return entry


def load_approved_channel_package(
    registry: ChannelPackageRegistry, approval: ApprovedChannelDistribution
) -> ChannelPackage:
    """Load a single preinstalled distribution selected by a trusted operator.

    Validate metadata, the entry point, and package ownership before import.
    The approved module and factory run as trusted code; they must not mount
    transports, install workspace instances or activate traffic while loading.
    Only a complete package with the pinned slug reaches the atomic registry.
    """
    if not isinstance(approval, ApprovedChannelDistribution):
        raise ChannelPackageDiscoveryError("An exact channel approval is required")
    if not isinstance(approval.slug, str):
        raise ChannelPackageDiscoveryError("An exact channel approval is required")
    if registry.resolve(approval.slug) is not None:
        raise ChannelPackageDiscoveryError("Channel package is already registered")
    try:
        entry = _approved_entry(approval)
        factory = entry.load()
        if not callable(factory) or iscoroutinefunction(factory):
            raise ChannelPackageDiscoveryError("Expected a synchronous channel factory")
        package = factory()
        if (
            not isinstance(package, ChannelPackage)
            or getattr(package.definition, "slug", None) != approval.slug
        ):
            raise ChannelPackageDiscoveryError(
                "Factory did not return the approved channel"
            )
        registry.register(package)
        return package
    except ChannelPackageDiscoveryError:
        raise
    except Exception:
        raise ChannelPackageDiscoveryError(
            "Approved channel could not be registered"
        ) from None
