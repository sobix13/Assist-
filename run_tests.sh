#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
assistant_python="${PYTHON:-.venv/bin/python}"
"$assistant_python" -m unittest discover -s tests -t . -v
"$assistant_python" -m compileall -q maestro scripts main.py
for assistant_script in scripts/*.sh run_tests.sh; do
    bash -n "$assistant_script"
done
