# Repository

Maestro 1.0.0 is published as one source repository containing both chat interfaces and the shared broker: https://github.com/sobix13/Assist-. The GitHub repository name is Assist-; the application, modules, panels and service units are Maestro.

The TAR and ZIP contain identical files: source, exact dependencies, installation/update/rollback helpers, four systemd templates including the optional witness, example configurations, tests/fixtures, guides, research, validation evidence and source checksums. Runtime data, credentials, private keys, installed virtual environments and generated deployment state are excluded.

Existing Rip Cars project names appear only in optional integration presets, source contracts and compatibility references. Maestro itself is personal and not branded as one of those bots.

The GitHub Actions workflow repeats installation and tests on Ubuntu/Python 3.12 with Restic. Its result is shown in the repository's Actions tab. `VALIDATION.json` records the isolated release-build execution before publication. Publication does not change those historical test records. Do not commit `.env`, root policy, adapter enrollment, database files, uploads, jobs, packages, frontend sessions or Restic credentials.

Source is at the repository root. Versioned source archives, standalone guides, validation records and SHA256SUMS are under `releases/1.0.0`. The source manifest describes the archive's exact source bytes. Publishing code is distinct from installing services on the VPS.

## Install from GitHub

On the VPS, use a new source folder. Do not clone over an existing folder:

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip restic ca-certificates
sudo git clone https://github.com/sobix13/Assist-.git /root/maestro-github
sudo bash /root/maestro-github/scripts/install.sh /root/maestro-github
sudo /opt/maestro/current/.venv/bin/python /opt/maestro/current/scripts/configure.py --interfaces both
sudo /opt/maestro/current/.venv/bin/python /opt/maestro/current/scripts/configure_backups.py --init
sudo systemctl enable --now maestro-agent.service maestro-discord.service maestro-telegram.service
```

Use `--init` only for a new Restic repository. The complete owner, backup, Telegram and Discord setup is in DEPLOYMENT.md. The executable release lives under `/opt/maestro`; the Git clone is only its reviewed source.
