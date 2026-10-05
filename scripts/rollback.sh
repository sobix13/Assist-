#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" -ne 0 ]]; then
    echo "Run with sudo"
    exit 1
fi
assistant_base=/opt/maestro
assistant_previous="$(readlink -f "$assistant_base/previous")"
if [[ "$assistant_previous" != "$assistant_base/releases/"* || ! -f "$assistant_previous/STAGED_OK" ]]; then
    echo "No tested previous release is available"
    exit 1
fi
assistant_running=()
for assistant_unit in maestro-agent.service maestro-discord.service maestro-telegram.service; do
    if systemctl is-active --quiet "$assistant_unit"; then assistant_running+=("$assistant_unit"); fi
done
if [[ ${#assistant_running[@]} -gt 0 ]]; then systemctl stop "${assistant_running[@]}"; fi
ln -sfn "$assistant_previous" "$assistant_base/current.next"
mv -Tf "$assistant_base/current.next" "$assistant_base/current"
install -m 0644 "$assistant_previous/maestro-discord.service" /etc/systemd/system/maestro-discord.service
install -m 0644 "$assistant_previous/maestro-agent.service" /etc/systemd/system/maestro-agent.service
if [[ -f "$assistant_previous/maestro-telegram.service" ]]; then
    install -m 0644 "$assistant_previous/maestro-telegram.service" /etc/systemd/system/maestro-telegram.service
else
    systemctl disable maestro-telegram.service || true
    rm -f /etc/systemd/system/maestro-telegram.service
    assistant_remaining=()
    for assistant_unit in "${assistant_running[@]}"; do
        if [[ "$assistant_unit" != maestro-telegram.service ]]; then assistant_remaining+=("$assistant_unit"); fi
    done
    assistant_running=("${assistant_remaining[@]}")
fi
systemctl daemon-reload
if [[ ${#assistant_running[@]} -gt 0 ]]; then systemctl start "${assistant_running[@]}"; fi
echo "Previous code release restored. Runtime databases and credentials were not replaced."
