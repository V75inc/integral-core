"""Build the test-only external channel wheel using only Python's stdlib."""

import base64
import csv
import hashlib
import io
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

DISTRIBUTION = "integral_external_channel_fixture"
VERSION = "1.0.0"
METADATA_DIR = f"{DISTRIBUTION}-{VERSION}.dist-info"
WHEEL_NAME = f"{DISTRIBUTION}-{VERSION}-py3-none-any.whl"


def build(output: Path) -> Path:
    """Build a valid local wheel with an isolated entry point and RECORD."""
    sample = (
        Path(__file__).resolve().parents[4]
        / "examples"
        / "reference-managed-text-channel"
    )
    modules = {
        f"{DISTRIBUTION}.py": (sample / f"{DISTRIBUTION}.py").read_bytes(),
        f"{METADATA_DIR}/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: integral-external-channel-fixture\n"
            f"Version: {VERSION}\n"
            "Summary: Test-only Integral managed channel fixture\n"
        ).encode(),
        f"{METADATA_DIR}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: integral-test-fixture\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ).encode(),
        f"{METADATA_DIR}/entry_points.txt": (
            "[integral.managed_channels.v1]\n"
            f"external_test_channel = {DISTRIBUTION}:build_channel\n"
        ).encode(),
    }
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, data in modules.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        writer.writerow([name, "sha256=" + digest.decode(), len(data)])
    writer.writerow([f"{METADATA_DIR}/RECORD", "", ""])
    output.mkdir(parents=True, exist_ok=True)
    wheel = output / WHEEL_NAME
    with ZipFile(wheel, "w", ZIP_DEFLATED) as archive:
        for name, data in modules.items():
            archive.writestr(name, data)
        archive.writestr(f"{METADATA_DIR}/RECORD", record.getvalue())
    return wheel


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
