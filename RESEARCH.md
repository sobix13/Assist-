# Research and decisions

Research date: 2026-10-05. This document separates repository observations from actual VPS state. A retained earlier repository review recorded seven accessible repositories through the owner account; the Maestro build also inspected the existing Verifier source contract locally. This inventory is historical source evidence, not a fresh live VPS scan or proof that every repository is currently deployed. Repository contents do not show which services are currently deployed.

## Reviewed projects

README files, recursive trees and relevant operational sources were inspected. This was an integration inventory, not a complete security audit or a rerun of every project's tests.

| Repository | Observed release | Integration finding |
|---|---:|---|
| sobix13/Ripcars-Gate | 1.0.2 | Independent membership/role ownership and shared coordination protocol |
| sobix13/Ripcars-Crew | 1.0.1 | Independent moderation/support ownership |
| sobix13/Ripcars-Raffle | 1.0.1 | Independent raffle state; never replay member/draw operations |
| sobix13/Verify-bot | 1.0.1 | Independent verification/holdings data and scheduling |
| sobix13/FUTARCHIST | 2.1.2 | Bot and web units, shared SQLite, `/ready`, worker heartbeats |
| sobix13/zone | 3.0.0 | Melee main and separate recovery services; distinct readiness endpoints |
| sobix13/gmpcbot | Source inspected | Express/SQLite web application; no dedicated readiness route; README and package start instructions differ |

A previously mentioned `gpt-vps-bridge` lookup returned 404. Its existence/access and deployed state were not established. Other private repositories or VPS workloads may exist; host inventory and explicit target enrollment accommodate them.

FUTARCHIST's bot heartbeat components are poller/inbox/outbox/maintenance, plus web for its web unit. The adapter reads these records without importing or executing application code. Melee Zone already has its own recovery budget and controller, so Maestro observes it by default. The gmpcbot repository is not automatically assumed to represent a working Telegram worker; enroll its actual service and health signal after discovery.

## Design choices

1. One root broker with two unprivileged interfaces avoids duplicate recovery engines and divergent settings. Separate credentials and transport-scoped identities remain necessary.
2. Owner IDs are enrolled locally, not inferred from Discord roles or Telegram usernames. Shared job access requires an explicit local owner link.
3. Passive checks run automatically. Unknown loaded services/containers get stable disabled cards every five minutes; they are not restarted merely because inventory found them.
4. Repair recipes are fixed argument arrays and known triggers, enrolled in a protected local file. No model generates or executes a fix.
5. Restic supplies encrypted incremental filesystem snapshots. SQLite uses its online backup API; other stores need a consistent export or paused writer. A live filesystem walk is not a globally atomic multi-store snapshot.
6. Backup exit status must be zero, repository metadata checks must pass, and this checkpoint's exported databases/manifest must actually restore. Failure blocks execution.
7. Only declared code/configuration paths are eligible for automatic recipe rollback. New or existing database content stops file rollback. Full restores are staged offline for explicit review.
8. Full retained output needs a separate private spool; a short chat preview is not a complete log. Redaction operates on accumulated text to cover secrets split across process chunks.
9. Telegram long polling runs independently of its input worker and report delivery. An existing webhook or second poller is treated as a configuration conflict, not silently removed.
10. A witness outside the VPS is needed to observe host/listener loss when all on-host processes stop. This optional witness cannot repair an offline host or validate all application flows.

## Primary sources

These sources were consulted directly. The recommendations above are engineering choices based on them, not copied vendor guarantees.

- [Telegram Bot API](https://core.telegram.org/bots/api): long polling, webhooks, callback buttons, file transfer and retry parameters. Maestro deliberately uses tighter 8 MiB upload and 6 MiB outgoing-part limits.
- [Restic scripting](https://restic.readthedocs.io/en/stable/075_scripting.html): JSON results and failure exit codes, including partially unreadable backups.
- [Restic repository checks](https://restic.readthedocs.io/en/stable/045_working_with_repos.html): metadata checks and rotating data subsets.
- [Restic restore](https://restic.readthedocs.io/en/stable/050_restore.html): explicit target directories and restored-file verification.
- [SQLite online backup API](https://sqlite.org/backup.html): consistent snapshots while the source database is live.
- [PostgreSQL pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html): consistent database exports; cluster-wide data and roles require separate consideration.
- [Docker inspect](https://docs.docker.com/reference/cli/docker/container/inspect/): inspecting container state. Maestro requests State only, not environment values.
- [systemd service configuration](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.service.xml): service state, restart/watchdog supervision.
- [Discord application commands](https://docs.discord.com/developers/interactions/application-commands): interaction-based administration.

## Remaining environment-specific inputs

Actual VPS paths, deployment units, database engines, backup backend credentials and bot tokens must be supplied locally. Telegram owner enrollment defaults to the supplied user ID 313342234; Discord owner/server IDs are independent. PostgreSQL/MySQL/Redis/container stores require application-specific consistent coverage. Provider-level VM snapshots, replicas and distributed transaction rollback require their own adapters and credentials. This package does not pretend those unknown inputs were discovered or tested remotely.


## Maestro requirements and considered alternatives

| Requirement | Chosen implementation | Reason |
|---|---|---|
| Personal bot outside Rip Cars branding | Maestro module, units, guides and panels | Existing project names remain only in genuine compatibility integrations |
| Discord and Telegram independently usable | Two unprivileged interfaces, one root broker | Separate sessions/outboxes without competing mutation engines |
| Owner can grant/revoke others | Expiring observers/operators scoped to service IDs | Full arbitrary-root delegation is unnecessary and broadens the trusted boundary |
| New VPS workload appears automatically | Five-minute passive service/container discovery with stable cards | Discovery can show a card before deciding its recovery ownership |
| Configure Verifier from Telegram | Source/schema-pinned typed adapter and own-bot REST provisioning | Generic guessed SQL or human-account impersonation is not a reliable setup contract |
| Application changes checked first | Type/permission/revision preflight, checkpoint, postcheck | A UI confirmation alone does not validate an application change |
| Preserve administrator/other-bot work | Shared lease, explicit ownership, baseline/revision checks, no blind adoption | Names alone do not establish ownership |
| Categorized downloadable backups | ZIPs restored from verified snapshot exports | Zipping a live SQLite/WAL pair is not an online-backup API substitute |
| No AI dependency | Fixed diagnostic families, declared recipes and typed adapters | Unknown failures need evidence/reporting rather than invented fixes |
| VPS completely offline | Optional independent second-host witness | A stopped on-host process cannot send an outage message |

Two complete independent repair brokers were rejected because they'd race on settings, backups and recovery. A full Telegram mirror of every public Discord member workflow was also rejected: Maestro is an administrative interface, while each application continues to handle its own user interactions. Generic arbitrary application SQL writes were rejected in favor of versioned contracts.

The staged approach is credentials/coverage enrollment, private UI wizard, passive checks, manual reversible actions, then narrow automatic repair opt-ins. This is a deliberate operating sequence, not a requirement to manually rerun hourly/daily checks.

## Sources checked for this extension

The Telegram Bot API, SQLite online backup API, Restic restore documentation and Discord application-command documentation were consulted again for this extension. The GitHub-owned checkout/setup-python documentation was checked for the CI workflow's supported v6 actions. Source contracts and tests, rather than a source's marketing claims, determine the delivered behavior. Dates and package test evidence appear in VALIDATION.json.

- https://core.telegram.org/bots/api
- https://sqlite.org/backup.html
- https://restic.readthedocs.io/en/stable/050_restore.html
- https://docs.discord.com/developers/interactions/application-commands
- https://github.com/actions/checkout
- https://github.com/actions/setup-python
