#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $# -ne 3 ]]; then
    echo 'Usage: launch_role_update.sh MAESTRO_COMMIT RAFFLE_COMMIT VERIFIER_COMMIT' >&2
    exit 2
fi
for role_update_ref in "$@"; do
    [[ "$role_update_ref" =~ ^[0-9a-f]{40}$ ]] || { echo 'Use exact reviewed commit IDs.' >&2; exit 2; }
done
[[ $EUID -eq 0 ]] || { echo 'Run with sudo bash.' >&2; exit 1; }
umask 022
for role_update_bin in git python3 systemd-run; do
    command -v "$role_update_bin" >/dev/null || { echo "Missing: $role_update_bin" >&2; exit 1; }
done
role_update_source="$(mktemp -d /var/tmp/ripcars-role-source.XXXXXXXX)"
role_update_names=(maestro raffle verifier)
role_update_repos=(Assist- Ripcars-Raffle Verify-bot)
role_update_refs=("$@")
for role_update_index in 0 1 2; do
    role_update_path="$role_update_source/${role_update_names[$role_update_index]}"
    git init -q "$role_update_path"
    git -C "$role_update_path" remote add origin "https://github.com/sobix13/${role_update_repos[$role_update_index]}.git"
    git -C "$role_update_path" fetch --depth=1 origin "${role_update_refs[$role_update_index]}"
    git -C "$role_update_path" checkout --detach -q FETCH_HEAD
done
role_update_unit="ripcars-role-update-$(date -u +%Y%m%d%H%M%S)"
systemd-run --no-block --unit="$role_update_unit" \
    --property=Type=oneshot --property=TimeoutStartSec=infinity --property=RemainAfterExit=yes \
    /usr/bin/python3 "$role_update_source/maestro/scripts/update_role_raffles.py" \
    --raffle "$role_update_source/raffle" --verifier "$role_update_source/verifier"
printf '%s.service\n' "$role_update_unit" > /root/ripcars-role-update.unit
printf 'Follow progress: journalctl -u %s.service -f -n 30 --no-pager -o cat\n' "$role_update_unit"
