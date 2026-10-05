#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" -ne 0 ]]; then
    echo "Run with sudo"
    exit 1
fi
assistant_source="$(realpath "${1:?Provide a reviewed extracted release directory}")"
assistant_update_unit="maestro-update-$(date -u +%Y%m%d%H%M%S)"
# Detach from the agent's cgroup before the installer stops the old agent.
systemd-run --unit="$assistant_update_unit" --collect /bin/bash "$assistant_source/scripts/install.sh" "$assistant_source"
echo "Independent update unit: $assistant_update_unit"
echo "Inspect: journalctl -u $assistant_update_unit --no-pager"
