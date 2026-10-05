# Deployment: Maestro 1.0.0

Target: Ubuntu 24.04 or equivalent Linux with systemd, Python 3.12, and root/sudo access. Use a dedicated Discord bot and dedicated Telegram bot. The two interfaces share one broker on the VPS. Do not install two brokers against the same state.

Source archives may live under `/root`. Runtime layout is `/opt/maestro/current`, `/etc/maestro`, `/var/lib/maestro-agent`, `/var/lib/maestro`, `/var/lib/maestro-telegram`, and `/run/maestro`.

## 1. Copy the release from Windows

Put `maestro-1.0.0.tar.gz` and `SHA256SUMS` in `C:\Users\macbook\Desktop\files`. In PowerShell, replace only the VPS address:

```powershell
$MaestroVps = "YOUR_VPS_IP"
scp "C:\Users\macbook\Desktop\files\maestro-1.0.0.tar.gz" "root@${MaestroVps}:/root/"
scp "C:\Users\macbook\Desktop\files\SHA256SUMS" "root@${MaestroVps}:/root/"
ssh "root@${MaestroVps}"
```

If you connect as `memecult`, use:

```powershell
$MaestroVps = "YOUR_VPS_IP"
scp "C:\Users\macbook\Desktop\files\maestro-1.0.0.tar.gz" "memecult@${MaestroVps}:/tmp/"
scp "C:\Users\macbook\Desktop\files\SHA256SUMS" "memecult@${MaestroVps}:/tmp/"
ssh "memecult@${MaestroVps}"
```

Then on the VPS:

```bash
sudo mv /tmp/maestro-1.0.0.tar.gz /root/
sudo mv /tmp/SHA256SUMS /root/
sudo -i
```

Verify the archive before extraction. This command checks just the downloaded TAR line, even when other files in the full checksum list were not downloaded:

```bash
cd /root
python3 - <<'PYCODE'
from pathlib import Path
import hashlib
name = 'maestro-1.0.0.tar.gz'
expected = next(line.split()[0] for line in Path('SHA256SUMS').read_text().splitlines() if line.split()[-1] == name)
actual = hashlib.sha256(Path(name).read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit('Checksum mismatch. Do not extract or install.')
print('Archive SHA-256 verified:', actual)
PYCODE
```

## 2. Install dependencies and the tested release

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip restic ca-certificates
cd /root
tar -xzf maestro-1.0.0.tar.gz
sudo bash /root/maestro/scripts/install.sh /root/maestro
```

The installer creates dedicated accounts and a fresh virtual environment, installs the locked requirements, runs tests, then installs the unit templates. First installation waits for owner/backup enrollment before starting services. On later updates it requires a verified checkpoint before switching code. Never extract a new release over a previous source folder; use a fresh extraction directory.

Node is optional and required only for Node file jobs. Docker is optional and required only for Docker targets. PostgreSQL/MySQL/Redis exporters are installed/enrolled only for stores you actually use. Backups need enough space for temporary consistent exports and an encrypted repository; review volumes and capacity in [BACKUPS.md](BACKUPS.md).

If a previous personal assistant is already installed, pause its monitoring/recovery and stop its controller before enabling Maestro. This is a new product/configuration path; its installer does not disable unrelated services or silently migrate the previous controller's policy. Review old target settings and backup credentials, then enroll them into Maestro. Keep existing application bots running. Running two independent doctor controllers with overlapping recovery ownership is unsupported.

## 3. Create bots and enroll your identities

Discord: create an application/bot in the Developer Portal, invite it with `bot` and `applications.commands`, and provide a private server/channel. Report permissions are View Channel, Send Messages, Read Message History, Embed Links, and Attach Files. Manage Channels is needed only if using Maestro's optional private report-channel creation button. Administrator and privileged member/message-content intents are unnecessary for Maestro itself.

Telegram: create Maestro through BotFather. Use a dedicated token with no second poller or active webhook. The numeric ID **313342234** is the default owner user ID; verify that it is your account. It is not a bot ID or username. Telegram groups are rejected.

```bash
sudo /opt/maestro/current/.venv/bin/python /opt/maestro/current/scripts/configure.py --interfaces both
```

The root-local wizard asks for the Discord owner ID, Discord server ID/token, and Telegram owner ID/token. Token input is hidden. Separate credentials are stored in root-owned mode-0600 files; two owner identities are explicitly linked for shared history. Existing owners cannot be replaced through chat.

To install only one interface initially, choose `--interfaces telegram` or `--interfaces discord` instead. To later add the missing interface and link both identities:

```bash
sudo /opt/maestro/current/.venv/bin/python /opt/maestro/current/scripts/configure.py --interfaces both --add-interface
sudo systemctl restart maestro-agent.service
```

Do not overwrite an enrolled owner ID. Identity replacement requires deliberate local policy review. Reboot commands are disabled unless this wizard is run with `--enable-reboot`.

## 4. Configure encrypted backup coverage

Use an off-host Restic repository or a reviewed mounted backup volume. A repository only on this VPS cannot survive loss of that disk/VPS.

```bash
sudo /opt/maestro/current/.venv/bin/python /opt/maestro/current/scripts/configure_backups.py --init
```

Use `--init` only for a new repository. Keep the existing repository password when enrolling an initialized one. The wizard asks for backend/password, filesystem roots, extra SQLite stores, and explicit coverage acknowledgment. Default filesystem root `/` excludes virtual/temp/cache locations and the backup repository. Other engines require fixed consistent exports or paused writers.

The first full checkpoint can take time. Subsequent snapshots are incremental, but each authorized execution still requires a fresh verified checkpoint. Read-only setup and monitoring can work before execution coverage is ready.

## 5. Start the selected interfaces

For both:

```bash
sudo systemctl enable --now maestro-agent.service maestro-discord.service maestro-telegram.service
sudo systemctl status maestro-agent.service maestro-discord.service maestro-telegram.service --no-pager -l
```

For Telegram only:

```bash
sudo systemctl enable --now maestro-agent.service maestro-telegram.service
```

For Discord only:

```bash
sudo systemctl enable --now maestro-agent.service maestro-discord.service
```

Open private Telegram `/start` and Discord `/maestro panel`. Telegram's five-step wizard covers identity, inventory, backup coverage, schedules, and activation. Confirm actual service paths and health signals. Discord also needs a private report channel. Automatic recovery remains disabled when the wizard completes.

Every five minutes new services are added as cards, even while monitoring is paused. New cards are disabled for background monitoring/recovery and initially retain external recovery ownership. Open a card to inspect/check/log it; deliberately enable background checks and select recovery ownership if appropriate.

## 6. Optional application setup from Telegram

Maestro can configure the existing Verifier without you repeatedly running manual database commands. It needs one local enrollment of the versioned adapter and existing protected credential paths:

```bash
sudo /opt/maestro/current/.venv/bin/python /opt/maestro/current/scripts/configure_applications.py
```

Choose the actual Maestro service target ID and existing Verifier paths/server ID. No setting is changed by enrollment. In Telegram use Application settings, select Verifier, set the ordinary member role, configure feature/provider fields and role rules, then review Create channels & roles, Publish panel, and Activate. See [APPLICATIONS.md](APPLICATIONS.md). Verifier must already have its own working Discord identity; Maestro does not impersonate your personal Discord account.

## 7. Health and journals

```bash
sudo journalctl -u maestro-agent.service -n 100 --no-pager -l
sudo journalctl -u maestro-discord.service -n 100 --no-pager -l
sudo journalctl -u maestro-telegram.service -n 100 --no-pager -l
```

Use Backend health, target Check/Logs, and Guide in either interface. Complete [ACCEPTANCE.md](ACCEPTANCE.md) on your real VPS. The cloud test report is evidence of the package's behavior, not a deployment receipt for your server.

## 8. Later update and code rollback

Extract a reviewed later release into a new source directory:

```bash
sudo bash /opt/maestro/current/scripts/update.sh /root/REVIEWED_NEW_SOURCE
```

The updater starts a detached systemd job, stages/tests the candidate, checkpoints the existing state, and remembers active interfaces. A startup failure selects the tested previous code. Inspect the returned update unit with `journalctl -u UNIT_NAME`.

```bash
sudo bash /opt/maestro/current/scripts/rollback.sh
```

Rollback preserves runtime databases and credentials. It does not undo remote effects or automatically restore an older database schema. Use verified offline restore staging for deliberate data recovery.

## 9. Optional second-host witness

Follow [docs/EXTERNAL_WITNESS.md](docs/EXTERNAL_WITNESS.md) only when another host is available. An on-host doctor cannot report a completely offline VPS without an independent observer. The default installer does not start the witness.
