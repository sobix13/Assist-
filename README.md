# Maestro 1.0.0

Maestro is a personal, deterministic VPS operations assistant. It has independent Discord and Telegram interfaces, one shared execution broker, and no AI dependency. It works with services beyond the Rip Cars projects.

Repository: [sobix13/Assist-](https://github.com/sobix13/Assist-). Install archives and SHA-256 checksums are in [releases/1.0.0](https://github.com/sobix13/Assist-/tree/main/releases/1.0.0).

The Telegram enrollment wizard defaults to the owner's numeric user ID **313342234**. The Discord owner ID and server ID are supplied separately. Superadmins are enrolled on the VPS; nobody gains ownership through a username, Discord role, or chat command.

## What is included

- Eleven clearly separated admin sections, contextual help, a five-step Telegram setup wizard, and 23 Discord subcommands under `/maestro`.
- Private owner sessions, optional expiring observer/operator grants for named services, immediate revocation, and separate frontend credentials/accounts.
- Automatic inventory every five minutes, including while monitoring is paused. Newly discovered loaded systemd services and Docker containers get stable clickable cards. Discovery preserves administrator edits and leaves unattended recovery disabled.
- A 60-second service watch, hourly reports, daily deep checks, persistent incidents, independent Discord/Telegram delivery acknowledgments, and successful-work heartbeat support.
- Service/container state, bounded logs, HTTP readiness, SQLite integrity, database/backup age, host disk/memory, TLS expiry, repository changes, and thirteen deterministic diagnostic families.
- Reviewed start/stop/restart actions, owner Bash execution, uploaded Python/Bash/Node files, stdin/EOF, cancellation, process-group deadlines, and private retained output. Root execution is a locally enrolled option.
- A fresh encrypted Restic checkpoint before execution or repair. Consistent database exports must restore and pass verification before the action begins.
- Categorized downloadable ZIPs: application source files, consistent database exports, and Maestro reports. They are assembled from the verified snapshot, contain checksums, and split into numbered transport parts when needed.
- Version-pinned Verifier settings, feature switches, role rules, channel IDs, public text, payment sources, channel/role provisioning, panel publication, and activation from Telegram or Discord.
- Locally declared typed JSON settings adapters for other applications. Preflight, revision checks, checkpoint, candidate validation, declared reload, postcheck, and bounded rollback protect changes.
- Fixed opt-in repair recipes, cooldowns/budgets, recovery ownership, shared setup leases, offline restores, isolated release staging, detached updates, and previous-code rollback.
- An optional second-host reachability witness for alerts when the VPS itself is unreachable.

## Two interfaces, one controller

`maestro-discord.service` and `maestro-telegram.service` have different Unix accounts, bot tokens, sessions, and delivery queues. They use `maestro-agent.service` for settings, jobs, backups, and mutations. A single-agent lock and serialized mutation queue prevent two independent repair engines from competing.

Confirmation remains bound to the originating actor. Explicitly linked owner identities share their job/output/upload history. Delegates see only assigned service health/logs and their own jobs. They cannot execute terminal code, download backups, edit applications, or grant others access.

## Install

Read [DEPLOYMENT.md](DEPLOYMENT.md) for copy/paste commands from `C:\Users\macbook\Desktop\files` through VPS startup. The archive can be kept under `/root`; executable releases are installed under `/opt/maestro`.

```bash
sudo bash /root/maestro/scripts/install.sh /root/maestro
sudo /opt/maestro/current/.venv/bin/python /opt/maestro/current/scripts/configure.py --interfaces both
sudo /opt/maestro/current/.venv/bin/python /opt/maestro/current/scripts/configure_backups.py --init
sudo systemctl enable --now maestro-agent.service maestro-discord.service maestro-telegram.service
```

Then open Discord `/maestro panel` and private Telegram `/start`. Review service cards, backup coverage, and schedules before completing the setup wizard. Automatic repair stays off until deliberately enabled.

## Documentation

| File | Contents |
|---|---|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Windows transfer, dependencies, enrollment, startup, updates, rollback |
| [TELEGRAM_GUIDE.md](TELEGRAM_GUIDE.md) | Private wizard, menus, service cards, terminal, files, applications, backups |
| [DISCORD_GUIDE.md](DISCORD_GUIDE.md) | Private report channel, branch panel, all command groups |
| [ACCESS.md](ACCESS.md) | Superadmin, observer/operator scopes, expiry, revoke |
| [APPLICATIONS.md](APPLICATIONS.md) | Verifier provisioning/activation and other typed settings adapters |
| [BACKUPS.md](BACKUPS.md) | Coverage, consistent exports, categorized ZIPs, restore |
| [ADMIN_GUIDE.md](ADMIN_GUIDE.md) | Monitoring, targets, deterministic repair, settings |
| [SECURITY.md](SECURITY.md) | Authentication, execution, sensitive state, controller boundaries |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, locks, persistence, failure behavior |
| [API_CONTRACT.md](API_CONTRACT.md) | Broker API and reviewed job types |
| [EXTENDING.md](EXTENDING.md) | New services, health signals, recipes, adapters |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Symptoms and practical diagnosis |
| [RESEARCH.md](RESEARCH.md) | Requirements, alternatives, primary sources, retained repository findings |
| [ACCEPTANCE.md](ACCEPTANCE.md) | Live VPS and chat acceptance steps |
| [REPOSITORY.md](REPOSITORY.md) | Contents and readiness for a later user-supplied GitHub repository |
| [VALIDATION.json](VALIDATION.json) | Actual test evidence and unperformed production checks |

## Development and evidence

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
sudo env PYTHON="$PWD/.venv/bin/python" bash run_tests.sh
```

Use Python 3.12 and install Restic for the real snapshot/restore tests. The installer creates a fresh virtual environment and runs the checks before activating a release. `RESTIC_TEST_BINARY` can select a test executable.

The delivered test evidence covers actual subprocesses, SQLite/WAL, real encrypted Restic restore, loopback HTTP Telegram/Discord fixtures, and mutual TLS. It does not claim access to the owner's VPS or production chat sessions. Unknown database engines, provider snapshots, and arbitrary remote side effects need their own adapters; a backup does not create universal undo.
