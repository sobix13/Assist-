#!/usr/bin/env python3
"""Build matching source TAR/ZIP packages and an exact SHA-256 source inventory."""
import hashlib
import json
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE = {".git", ".venv", "__pycache__", "data", "dist", "releases", "uploads", "jobs", 'outputs', 'checkpoints', 'restic-cache', 'packages', 'delegates'}


def sources():
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if not path.is_file() or path.is_symlink() or set(relative.parts) & EXCLUDE:
            continue
        if path.name in (".env", "frontend.env", "telegram.env", "external.env", "policy.json", "backup.json", "restic.env", "recipes.json", "applications.json", "SOURCE_MANIFEST.json") or any(x.startswith('rollback-') for x in relative.parts) or path.suffix in (".pyc", ".sqlite3", ".key", ".pem", ".rca", '.mca') or ".sqlite3-" in path.name:
            continue
        yield path


def build():
    version = (ROOT / "VERSION").read_text().strip()
    folder = ROOT / "releases" / version
    folder.mkdir(parents=True, exist_ok=True)
    files = list(sources())
    manifest = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    inventory = ROOT / "SOURCE_MANIFEST.json"
    inventory.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    files.append(inventory)
    tar_path = folder / ("maestro-" + version + ".tar.gz")
    zip_path = folder / ("maestro-" + version + ".zip")
    with tarfile.open(tar_path, "w:gz") as archive:
        for p in files:
            archive.add(p, arcname="maestro/" + str(p.relative_to(ROOT)), recursive=False)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for p in files:
            archive.write(p, arcname="maestro/" + str(p.relative_to(ROOT)))
    sums = "\n".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name for p in (tar_path, zip_path)) + "\n"
    (folder / "SHA256SUMS").write_text(sums)
    (folder / "SOURCE_MANIFEST.json").write_bytes(inventory.read_bytes())
    print(json.dumps({"version": version, "source_files": len(files), "archives": [str(tar_path), str(zip_path)]}, indent=2))


if __name__ == "__main__":
    build()
