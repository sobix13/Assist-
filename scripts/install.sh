#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" -ne 0 ]]; then
    echo "Run with sudo bash scripts/install.sh SOURCE_DIRECTORY"
    exit 1
fi
assistant_source="$(realpath "${1:-.}")"
assistant_base=/opt/maestro
assistant_version="$(cat "$assistant_source/VERSION")"
if [[ ! "$assistant_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Invalid version"
    exit 1
fi
for assistant_bin in python3 systemctl runuser; do
    command -v "$assistant_bin" >/dev/null || { echo "Missing $assistant_bin"; exit 1; }
done
python3 -m venv --help >/dev/null
getent group maestro >/dev/null || groupadd --system maestro
id maestro >/dev/null 2>&1 || useradd --system --gid maestro --home-dir /var/lib/maestro --shell /usr/sbin/nologin maestro
id maestro-telegram >/dev/null 2>&1 || useradd --system --gid maestro --home-dir /var/lib/maestro-telegram --shell /usr/sbin/nologin maestro-telegram
id maestro-runner >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/maestro-runner --shell /usr/sbin/nologin maestro-runner
install -d -m 0755 "$assistant_base/releases"
install -d -m 0700 /etc/maestro
install -d -m 0700 -o maestro -g maestro /var/lib/maestro
install -d -m 0700 -o maestro-telegram -g maestro /var/lib/maestro-telegram
install -d -m 0711 -o root -g root /var/lib/maestro-agent
assistant_candidate="$assistant_base/releases/$assistant_version-$(date -u +%Y%m%d%H%M%S)"
python3 "$assistant_source/scripts/stage_release.py" --source "$assistant_source" --destination "$assistant_candidate"
chown -R root:root "$assistant_candidate"
chmod -R go-w "$assistant_candidate"
if [[ -f /etc/maestro/policy.json ]]; then
    "$assistant_candidate/.venv/bin/python" "$assistant_candidate/scripts/local_checkpoint.py" --reason "install:$assistant_version"
fi
assistant_previous=""
if [[ -L "$assistant_base/current" ]]; then
    assistant_previous="$(readlink -f "$assistant_base/current")"
fi
assistant_running=()
for assistant_unit in maestro-agent.service maestro-discord.service maestro-telegram.service; do
    if systemctl is-active --quiet "$assistant_unit"; then
        assistant_running+=("$assistant_unit")
    fi
done
if [[ ${#assistant_running[@]} -gt 0 ]]; then
    systemctl stop "${assistant_running[@]}"
fi
if [[ -n "$assistant_previous" ]]; then
    ln -sfn "$assistant_previous" "$assistant_base/previous"
fi
ln -sfn "$assistant_candidate" "$assistant_base/current.next"
mv -Tf "$assistant_base/current.next" "$assistant_base/current"
install -m 0644 "$assistant_candidate/maestro-agent.service" /etc/systemd/system/maestro-agent.service
install -m 0644 "$assistant_candidate/maestro-discord.service" /etc/systemd/system/maestro-discord.service
install -m 0644 "$assistant_candidate/maestro-telegram.service" /etc/systemd/system/maestro-telegram.service
systemctl daemon-reload
if [[ ${#assistant_running[@]} -gt 0 ]]; then
    if ! systemctl start "${assistant_running[@]}"; then
        echo "New services failed. Restoring the tested previous code release."
        if [[ -n "$assistant_previous" && -f "$assistant_previous/STAGED_OK" ]]; then
            systemctl stop "${assistant_running[@]}" || true
            ln -sfn "$assistant_previous" "$assistant_base/current.next"
            mv -Tf "$assistant_base/current.next" "$assistant_base/current"
            for assistant_unit in maestro-agent.service maestro-discord.service maestro-telegram.service; do
                if [[ -f "$assistant_previous/$assistant_unit" ]]; then
                    install -m 0644 "$assistant_previous/$assistant_unit" "/etc/systemd/system/$assistant_unit"
                else
                    rm -f "/etc/systemd/system/$assistant_unit"
                fi
            done
            systemctl daemon-reload
            systemctl start "${assistant_running[@]}" || true
        fi
        echo "Inspect journals. Application data and credentials were preserved; the pre-update checkpoint remains available."
        exit 1
    fi
fi
echo "Staged release installed. Private state and all companion services were preserved."
echo "First install: sudo $assistant_candidate/.venv/bin/python $assistant_candidate/scripts/configure.py"
echo "Backups: sudo $assistant_candidate/.venv/bin/python $assistant_candidate/scripts/configure_backups.py --init"
echo "Then: sudo systemctl enable --now maestro-agent.service maestro-discord.service maestro-telegram.service"
