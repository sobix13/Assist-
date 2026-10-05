#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import subprocess
import venv
from pathlib import Path


def stage(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    version = (source / "VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or destination.exists() or source == destination or source in destination.parents:
        raise ValueError("Use a new staging directory outside a valid source release.")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git", ".venv", ".env", "__pycache__", "*.pyc", "*.sqlite3*", "policy.json", "frontend.env", "telegram.env", "external.env", "backup.json", "restic.env", "recipes.json", "applications.json", "releases", "dist", "uploads", "jobs", "data", "outputs", "checkpoints", "rollback-*", "restic-cache", 'packages','delegates'))
    venv.create(destination / ".venv", with_pip=True, symlinks=True)
    python = destination / ".venv/bin/python"
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(destination / "requirements-lock.txt")], check=True)
    env = {**os.environ, "PYTHON": str(python), "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run(["bash", "run_tests.sh"], cwd=destination, env=env, check=True)
    (destination / "STAGED_OK").write_text("Release " + version + ": isolated installation and tests passed.\n")
    return destination


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Stage and test without changing a running installation")
    p.add_argument("--source", required=True)
    p.add_argument("--destination", required=True)
    a = p.parse_args()
    print(stage(a.source, a.destination))
