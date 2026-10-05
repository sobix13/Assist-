#!/usr/bin/env python3
import hashlib
import json
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify():
    version = (ROOT / "VERSION").read_text().strip()
    folder = ROOT / "releases" / version
    manifest = json.loads((folder / "SOURCE_MANIFEST.json").read_text())
    manifest_bytes = (folder / "SOURCE_MANIFEST.json").read_bytes()
    expected = {**manifest, "SOURCE_MANIFEST.json": hashlib.sha256(manifest_bytes).hexdigest()}
    counts = {}
    for suffix in ("tar.gz", "zip"):
        archive_path = folder / ("maestro-" + version + "." + suffix)
        if suffix == "zip":
            with zipfile.ZipFile(archive_path) as archive:
                values = {name.removeprefix("maestro/"): hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}
        else:
            with tarfile.open(archive_path) as archive:
                values = {member.name.removeprefix("maestro/"): hashlib.sha256(archive.extractfile(member).read()).hexdigest() for member in archive if member.isfile()}
        if values != expected:
            raise ValueError("Archive inventory/hash mismatch: " + suffix)
        counts[suffix] = len(values)
    for line in (folder / "SHA256SUMS").read_text().splitlines():
        checksum, name = line.split("  ", 1)
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != checksum:
            raise ValueError("Package checksum mismatch.")
    print(json.dumps({"ok": True, "matching_files": counts}, indent=2))


if __name__ == "__main__":
    verify()
