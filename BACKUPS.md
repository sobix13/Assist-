# Backups and recovery

There are three different backup forms: encrypted execution checkpoints, portable categorized ZIP downloads, and encrypted Maestro-state downloads. A small encrypted Maestro-state download is not a whole-VPS checkpoint.

## Restic execution checkpoints

Default policy requires a fresh checkpoint before shell/file execution, service/container actions, repair recipes, reboot, offline restore jobs and an update to an existing installation. Read-only observations and file staging do not execute owner code and do not require this checkpoint. Resuming a writer paused by the checkpoint is mandatory compensation, even when that checkpoint fails.

A checkpoint consists of:

1. Protected local coverage/credential validation and process-level repository lock.
2. Optional pause of declared active writer units. Their prior active state is recorded before stopping.
3. Consistent SQLite online-backup copies and fixed database-export hooks. Enrolled local SQLite target files that exist are added automatically, including disabled targets.
4. SHA-256/size manifest and encrypted Restic backup of configured filesystem roots plus the export directory.
5. Exit status zero and repository metadata check.
6. Restore of this checkpoint's exports/manifest into a private verification directory, with checksums compared.
7. Resume of paused writers. Only then is the checkpoint marked completed and execution authorized.

Partial Restic success is failure. Missing declared databases, corrupt exports, timeout, failed metadata/restore/checksum or failed writer resumption block execution. Failed staging files remain private for investigation. A process crash marks its checkpoint interrupted and reports recorded paused writers on startup; it cannot promise a writer resumed if the host itself stopped. Review and use an explicit service action when needed.

Daily scheduled checkpoints use the same logic when configured and not in maintenance. Daily data reads rotate a `1/7` through `7/7` subset by UTC weekday; the default per-checkpoint metadata check is not a read of every byte of the whole repository.

## Coverage and consistency

Configuration: `/etc/maestro/backup.json`, root-owned 0600. Credentials: `/etc/maestro/restic.env`, root-owned 0600. The local wizard sets reviewed roots and stores secrets through hidden input. Default root `/` covers persistent accessible files except virtual/temp/cache locations, older private checkpoint staging trees and the local repository itself. Check mounts and exclusions for your workload.

A live filesystem walk does **not** provide one atomic instant across all services. SQLite exports are consistent individually. PostgreSQL/MySQL/Redis, Docker volumes, external databases and other stores require an appropriate export or paused writer. A VM/provider snapshot, replica or distributed transaction rollback needs a separate provider-specific adapter. This release does not claim such unknown stores are automatically safe.

Example protected configuration (review paths before use):

```json
{
  "coverage_reviewed": true,
  "paths": ["/"],
  "env_file": "/etc/maestro/restic.env",
  "exclude": ["/var/cache", "/var/lib/maestro-agent/restic-cache"],
  "databases": [
    {"name": "extra-store", "kind": "sqlite", "path": "/srv/extra/data.sqlite3"},
    {"name": "postgres-app", "kind": "export", "argv": ["/usr/sbin/runuser", "-u", "postgres", "--", "/usr/bin/pg_dump", "-Fc", "app_database"]}
  ],
  "quiesce_units": [],
  "timeout_seconds": 1800,
  "export_limit_mib": 512
}
```

The PostgreSQL example writes one database export to stdout. Enroll global roles and additional databases separately when needed. For MySQL, use a locally reviewed root-owned exporter with consistent transaction options and protected credential files. Do not put database passwords into argv. Export hooks are fixed locally and are not editable as arbitrary chat-supplied hooks.

The command environment is clean plus the protected backup environment. Backend/export credentials needed by an exporter must therefore be declared there or read by its reviewed hook. Independent export deadlines and size caps protect against a stuck/flooding exporter. Export failure prevents execution.

Use an encrypted off-host destination. Same-disk snapshots protect against some file changes, not host/disk loss. Keep a recovery copy of credentials/password elsewhere. Root access can change or destroy its own local backups; no on-host software can prevent an authorized root command from doing so. Use external immutable storage policies if that protection is needed.

## Offline full restore

Use `/maestro restore CHECKPOINT_ID` or `/restore CHECKPOINT_ID`. The broker first checkpoints the current state, then restores the selected completed snapshot into a **new private folder**, verifies restored content and recorded database-export checksums, and returns its path. It never overlays production or reverts fresh member data automatically.

For database recovery, use the consistent export recorded in the checkpoint manifest, not a possibly live-copied raw DB/WAL from the general filesystem tree. Inspect the restored files, stop writers deliberately, preserve current DB/WAL, verify version/schema compatibility, then plan the final switch. PostgreSQL/MySQL exports need their engine's restore procedure.

Retain multiple snapshots. This package does not automatically `forget`, prune or unlock repositories. Review snapshot retention and locks locally; an automatic cleanup must not erase the only recovery copy. Capacity warnings remain visible in monitoring.

## Bounded recipe rollback

A recipe can preserve up to 2,000 code/configuration entries and 32 MiB under specific reviewed roots. On failed action/health verification it restores those files and optionally runs a fixed rollback service action. Original modes/ownership and symlinks are recorded.

Existing or newly created database content, parent symlink changes and expanded entry limits stop automatic file rollback. Backups and operation details remain available; the result is escalated. Effects outside the declared roots, remote APIs, migrations and arbitrary shell code have no generic compensating transaction.

## Encrypted Maestro state download

Discord `/maestro backup` uses a password modal. Telegram `/maestro_backup PASSWORD` uses the owner's private chat. At least 12 password characters are required. A SQLite backup is integrity-checked and encrypted with PBKDF2-HMAC-SHA256 (600,000 iterations, random salt) and authenticated Fernet encryption.

The `.rca` contains Maestro SQLite state, including sensitive job source payloads. It excludes environment/policy files, uploaded bytes, workspaces, full output spools and companion databases. Unencrypted state is capped at 6 MiB to fit the local API. Larger state uses the VPS/Restic procedure. Encrypted bytes are divided into numbered 6 MiB parts when necessary; join **binary** parts in order before restoring and verify the advertised SHA-256.

```bash
cat maestro-backup.rca.part* > maestro-backup.rca
sha256sum maestro-backup.rca
/opt/maestro/current/.venv/bin/python /opt/maestro/current/scripts/restore_backup.py maestro-backup.rca /root/maestro-restored.sqlite3
```

Enter the password privately when prompted. This restores to a new offline database. Stop the agent before an intentional state swap. On restart outstanding reviews are invalidated and unfinished jobs become interrupted; commands are never replayed automatically.


## Portable categorized ZIP downloads

Telegram: Backups & restore > Download ZIP packages, or a service card > ZIP backup. Discord: `/maestro package`. Prepare selects an existing service target or `all`; a changed file/target scope invalidates a pending review.

Before packaging, the broker creates the same verified encrypted checkpoint used for execution. It restores the selected source files and recorded consistent database exports from that snapshot into a new private directory, checks export hashes, then creates ZIPs. It never substitutes a fresh live database copy for the verified export.

`all` produces a databases ZIP, per-application source ZIPs for declared install directories, and a Maestro reports ZIP. A target package includes selected source and matching target/export database data. Empty/unrecognized install paths do not create imaginary source coverage.

Each ZIP contains `MANIFEST.json` with its snapshot/checkpoint IDs, file hashes/sizes and discovery status. It is tested for ZIP integrity, retained privately and delivered with SHA-256. ZIPs are plaintext and may contain sensitive member/application data. They exclude source environment/credential-named files, dependencies, runtime uploads/jobs and the protected owner policy. This filter cannot certify that arbitrary source/database contents contain no secrets.

### Discovery and coverage

Database scanning recognizes the SQLite header, not just a filename. It is bounded to five seconds, 4,000 candidate entries, five directory levels and 100 SQLite candidates per inventory scan, and does not follow symlinks. Defaults include project inventory roots plus `/var/lib`; set root-local `database_roots` for additional locations or narrower scans. Unreadable candidates or scan limits mark discovery incomplete. The recorded roots/status describe a bounded scan, not proof that every store on a VPS was discovered.

Package requests include cached SQLite discoveries as consistent exports. Declared database stores and target SQLite paths are also checkpointed, including disabled targets. PostgreSQL/MySQL/Redis/remote stores still need explicit consistent hooks. An unrecognized store is not silently treated as a safe database export. Review coverage and manifest before calling a download a complete recovery set.

Source selection is bounded to 1,000 files/64 MiB total, 8 MiB per file, and declared source/document/config suffixes. Each ZIP group is capped at 512 MiB uncompressed. Larger workloads should use the encrypted filesystem checkpoint or select a smaller service. ZIP retention follows the configured daily retention; encrypted Restic snapshots are not automatically deleted.

### Join split downloads

Transport files are at most 6 MiB and use `.part001`, `.part002`, etc. The part hashes are checked during delivery. Preserve every part and join in exact numeric order; then compare the advertised whole-file SHA-256 before extracting. Example on Linux:

```bash
cat maestro-databases-CHECKPOINT.zip.part??? > maestro-databases-CHECKPOINT.zip
sha256sum maestro-databases-CHECKPOINT.zip
unzip -t maestro-databases-CHECKPOINT.zip
```

PowerShell example, run in the folder containing one package's parts:

```powershell
$MaestroArchive = "maestro-databases-CHECKPOINT.zip"
$MaestroOutput = [System.IO.File]::Create((Join-Path $PWD $MaestroArchive))
try {
    Get-ChildItem "$MaestroArchive.part*" | Sort-Object Name | ForEach-Object {
        $MaestroInput = [System.IO.File]::OpenRead($_.FullName)
        try { $MaestroInput.CopyTo($MaestroOutput) } finally { $MaestroInput.Dispose() }
    }
} finally { $MaestroOutput.Dispose() }
Get-FileHash $MaestroArchive -Algorithm SHA256
```

Replace `CHECKPOINT` with the actual download name. An unsplit `.zip` needs no concatenation. Copying a ZIP to a new bot/server does not by itself supply provider credentials, environment files, database restore procedure or an approved deployment plan.
