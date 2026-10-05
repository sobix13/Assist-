# Owner administration

Discord: `/maestro panel`. Telegram: `/start` then `/panel`, in the enrolled owner's private chat. The bot UI is English, with maroon Discord embeds. Discord roles and Telegram usernames do not grant terminal access.

## Panel branches

| Branch | Tasks |
|---|---|
| Setup & schedules | Activate/pause monitoring, schedules, private Discord report channel |
| Services & health | Add/edit targets, run hourly/daily checks |
| Host inventory | Read-only server scan, discover installed presets, enroll other apps |
| Recovery & diagnostics | Global opt-in, fixed diagnostics, maintenance, service actions, recipes |
| Terminal | Restricted runner or owner-authorized root Bash code |
| Files & execution | Stage, inspect, review a file or code with its files |
| Jobs & history | State, live preview, full output, stdin, EOF, cancellation |
| Backups & restore | Coverage, checkpoints, offline restores, exports, encrypted state downloads |
| Application settings | Versioned settings, Verifier provisioning/publication/activation |
| People & access | Scoped observer/operator grants, expiry and revoke |
| Guide | Setup, health and workflow help |

A target editor uses revision checks to prevent one interface overwriting another's newer configuration. Discord targets are paginated. For an unusually large target, its editor accepts partial JSON fields and preserves existing fields. Telegram also accepts partial JSON fields. Five-minute discovery adds stable service cards while preserving administrator edits. Uploaded entries beyond the Discord dropdown remain accessible through `/maestro run_file`.

## Schedule

After activation, watch checks run every 60 seconds; reports run hourly by default; daily deep checks run at 18:00 in `Asia/Tehran` by default. Change timezone, daily hour and hourly interval in Setup. Schedules and last-run records survive restarts. Recovery is off by default; monitoring does not require automatic changes.

Hourly reports also observe other failed systemd units without adopting or restarting them. Set `disk_roots` in the root-owned policy for additional persistent volumes and `inventory_roots` for additional project locations.

Daily checks add SQLite integrity, backup age, installed version, local certificate expiry, inventory and repository changes. When configured and not in maintenance, the daily cycle creates a checkpoint and rotates a Restic data subset. An hourly/watch check does not resolve a daily-only failure it did not recheck.

## Target definition

```json
{
  "id": "my-worker",
  "unit": "my-worker.service",
  "enabled": true,
  "expected_running": true,
  "auto_restart": false,
  "recovery_owner": "assistant",
  "driver": "systemd",
  "adapter": "generic",
  "database": "/srv/my-worker/data/worker.sqlite3",
  "install": "/srv/my-worker",
  "env_file": "/etc/my-worker.env",
  "url": "http://127.0.0.1:9000/ready",
  "expected_json_key": "ready",
  "heartbeat_file": "/srv/my-worker/state/last-success",
  "heartbeat_max_seconds": 180,
  "repo": "OWNER/REPOSITORY",
  "revision": 0
}
```

| Field | Meaning |
|---|---|
| `enabled` | Include this target in scheduled checks |
| `expected_running` | Whether an active process is expected; false represents a deliberate stop |
| `auto_restart` | Target opt-in to bounded automatic restart |
| `auto_recipe` | Optional exact locally enrolled recipe ID |
| `recovery_owner` | `assistant` or `external`; external prevents competing repairs |
| `driver` | `systemd` or `docker` |
| `container` | Exact Docker container name/ID when the Docker driver is used |
| `adapter` | `generic` or `futarchist` |
| `worker_mode` | FUTARCHIST `bot`, `web` or `all` |
| `database` | Optional SQLite path for integrity/checkpoint export; not a generic SQL server connection |
| `url` / `expected_json_key` | Readiness endpoint and optional Boolean-true JSON key; plain HTTP is loopback-only |
| `heartbeat_file` | File updated only after successful useful work, not simply on process startup |
| `backup_dir` / `backup_max_hours` | Optional application-backup age checks, 1–168 hours |
| `certificate_file` / `cert_warn_days` | Public certificate PEM and optional 1–60 day warning threshold |
| `install` / `env_file` | Installed version and secret-redaction/discovery inputs |
| `repo` | GitHub default-branch observation; never an automatic pull/install |
| `revision` | Copy the current revision when editing |

For Docker, use `unit: "docker.service"`, `driver: "docker"`, and its exact `container`. Its State health field is inspected; environment values are not exported. A nonzero container exit is distinguished from an intentional clean stop.

Discover installed presets reads the unit working directory and a single ordinary EnvironmentFile when available. Relative `DATABASE_PATH`/`DB_PATH` is resolved against that working directory. Complex multiple-env, quoted-path or custom deployment cases must be reviewed explicitly. Adding duplicate service/container targets is refused.

## Recovery

Both global recovery and target opt-in are required. A known locally enrolled automatic recipe also needs matching diagnostic triggers. The agent uses two observations separated by at least 30 seconds, a 15-minute cooldown, at most two attempts/hour and three/day. It does not compete with an external controller, violate a shared suite setup lease, restart intentionally stopped units, or automatically restart its own root agent.

Known permissions, credentials, database corruption/busy, missing dependencies, port conflicts and disk-full evidence block restart. A failed checkpoint blocks all repair execution. Fixed read-only diagnostics gather evidence before escalation. Recipes use a checkpoint, optional preflight, bounded file rollback and post-action health checks. See [EXTENDING.md](EXTENDING.md).

## Terminal and files

1. Enter code or stage a document/archive. Staging never executes it.
2. Inspect the manifest/content; choose runtime/profile/arguments.
3. Review the exact request and its digest. Confirm within five minutes.
4. The broker serializes mutations, creates and verifies a checkpoint, then executes.
5. Inspect state, exit code, preview and complete output before retrying anything non-idempotent.

Two jobs may be queued/active, but only one mutation executes at a time across both interfaces. File/code jobs use a clean environment and a private workspace. Bash/Python/Node runtimes must exist on the VPS. Project dependencies can be selected explicitly in shell code using that project's virtual-environment executable.

The default runner is a separate Unix account with no sudo privileges. It is not a container sandbox. The root profile is genuine owner-authorized root execution. No profile silently falls back to root.

Job timeout is bounded at 900 seconds. Preview is 128 KiB; the complete retained spool defaults to 32 MiB and can be set to 1–64 MiB. If the spool cap is exceeded, the process group is stopped and the result is `output_limited`. Downloads use text, gzip or numbered files. Cancelled/timed-out output remains retrievable. A restart marks unfinished jobs interrupted and never replays them.

## Useful commands

| Discord | Telegram |
|---|---|
| `/maestro health` | `/health` |
| `/maestro inventory` | `/inventory` |
| `/maestro check` | `/check` or `/check TARGET daily` |
| `/maestro terminal` | `/exec runner CODE` or `/exec root CODE` |
| `/maestro upload` | Send a document; optional caption `/exec PROFILE CODE` |
| `/maestro run_file` | `/run_json` with file payload JSON |
| `/maestro job JOB_ID` | `/job JOB_ID` |
| `/maestro output JOB_ID` | `/output JOB_ID` |
| Job buttons: stdin/EOF/cancel | `/stdin JOB_ID TEXT`, `/eof JOB_ID`, `/cancel_job JOB_ID` |
| `/maestro service` | `/service restart example.service` |
| `/maestro container TARGET ACTION` | `/container ACTION TARGET` |
| `/maestro recipes`, `/maestro repair RECIPE` | `/repair RECIPE` |
| `/maestro checkpoint`, `/maestro checkpoints` | `/checkpoint`, `/checkpoints` |
| `/maestro restore CHECKPOINT_ID` | `/restore CHECKPOINT_ID` |
| `/maestro backup` | `/maestro_backup PASSWORD` |
| `/maestro export`, `/maestro guide` | `/export`, `/guide` |

Telegram also supports `/execfile PROFILE UPLOAD_ID CODE`, `/preview UPLOAD_ID ENTRY`, `/diagnostic NAME`, `/reboot REBOOT HOSTNAME`, and `/cancel` to clear an input step. A plain chat message is never executed without the explicit terminal input mode. Private messages containing passwords remain in your chat history unless you remove them; Discord's backup password modal avoids posting a password as a command message.

## Delivery and persistence

Reports are queued independently for both identities. Acknowledgment in Discord does not consume Telegram's report. Delivery is at-least-once; network failure between send and ack may repeat the same report ID. Up to 2,000 recent outbox events are retained; a prolonged outage can discard older events. Download operational exports to review history.

Private job outputs are sent with completion reports. If Discord DMs are disabled, download them from the panel. Report delivery and job history remain separate. Telegram persists update offsets before handling so a crash cannot replay an owner command; an accepted but unfinished input may require reopening the panel and checking history. Frontend/backend restarts invalidate stale broker confirmations.

Retention applies to ordinary audit/snapshots/jobs/uploads/output. Restic snapshots, checkpoint records and rollback directories are not automatically deleted. Review capacity and retention deliberately using the backup guide.


## Personal access and applications

Telegram defaults to superadmin 313342234; Discord uses a separately enrolled numeric identity. Only the protected VPS policy controls superadmins. Expiring observers/operators are granted through People & access. See ACCESS.md, TELEGRAM_GUIDE.md and DISCORD_GUIDE.md.

Application settings uses locally enrolled versioned contracts and typed fields. Verifier operations can create missing channels/roles and publish its persistent panel using its own token and shared lease. See APPLICATIONS.md. Categorized ZIPs come from verified snapshots; see BACKUPS.md.

The stored `recovery_owner` value `assistant` means Maestro owns recovery. This is a stable configuration enum, while `external` delegates recovery to another controller. It is not a second controller process.
