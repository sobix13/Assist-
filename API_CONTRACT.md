# Local broker API

One JSON request and response per Unix or mutually authenticated loopback TLS connection. Maximum message: 12 MiB. Request fields: `token`, `actor`, `action`, `params`. A successful reply is `{ "ok": true, "result": ... }`; a failure is `{ "ok": false, "error": ... }`.

All calls require an eligible transport-scoped superadmin or scoped unexpired delegate. The access layer restricts every action/target; see ACCESS.md. The frontend credential and Unix peer UID or pinned TLS identity must match. Callers should inspect job history after disconnect rather than blindly retry a non-idempotent request.

| Action | Parameters / result |
|---|---|
| `status` | Version, settings, targets, incidents, scheduler, checkpoint coverage, recent operations |
| `settings` | Current settings with revision |
| `save_settings` | `revision`, `changes`; strict known fields and ranges |
| `targets` | Current enrolled target definitions |
| `save_target` | Complete target JSON and current revision; no duplicate unit/container |
| `inventory` | Refresh bounded passive inventory; adds stable disabled cards for new loaded services/containers |
| `inspect_target` | Target ID; fill only blank unit-derived metadata with revision protection |
| `whoami` | Current actor, superadmin/observer/operator, scope and expiry |
| `access_list`, `access_grant`, `access_revoke` | Superadmin-only; numeric actor, role, target IDs, seconds and current revision |
| `applications`, `application` | Locally enrolled adapter list or ID/settings/revision |
| `package_part` | Artifact ID and aligned offset; owner-only private part/checksums |
| `discover` | Enroll installed **presets**, read ordinary unit paths/environment, preserve recovery flags |
| `check` | `kind`: watch/hourly/daily; optional target; manual checks do not repair |
| `diagnostic` | Exact fixed diagnostic name |
| `logs` | Target ID; bounded redacted journal/container logs |
| `checkpoint_status` | Protected coverage readiness, no credentials |
| `checkpoints` | Latest 30 checkpoint records and verification details |
| `recipes` | Locally enrolled fixed recipe definitions, no arbitrary chat editing |
| `prepare` | `kind`, `payload`; exact preview/digest, one-time token and expiry |
| `confirm` | Review ID/token/digest; atomically consumed; queues job |
| `discard` | Review ID/token; irrevocably consumes the pending request |
| `job`, `jobs` | ID/result or latest job metadata; linked owner identity checks |
| `job_output` | ID, byte offset, optional compressed flag; 6 MiB part and next offset/hash/metadata |
| `cancel`, `stdin`, `eof` | Job ID; optional input text, never copied into ordinary audit |
| `upload` | Safe name and base64 bytes; stages only, returns content-hash manifest |
| `preview`, `uploads` | Upload ID/entry or latest owner-visible manifests |
| `events` | Up to ten pending events for this recipient |
| `ack` | Event ID; acknowledges this recipient only |
| `export` | Settings, targets, incidents, bounded audit/jobs, inventory/checkpoint/operation metadata |
| `backup` | Password; encrypted bounded Maestro-state bytes and SHA-256 |

## Job payloads

- `shell`: code, profile (`runner`/`root`), optional cwd/upload, timeout (1–900).
- `file`: upload, exact entry, runtime (`python`/`bash`/`node`), args array, profile, timeout.
- `service`: exact `.service` name and start/stop/restart/reset-failed/enable/disable/reload action.
- `container`: enrolled Docker target and start/stop/restart action.
- `recipe`: exact local recipe ID; prepared payload pins its configuration digest.
- `checkpoint`: empty payload; creates a verified snapshot job.
- `package`: target ID or `all`; pins scope and creates consistent categorized ZIPs from the verified snapshot.
- `application`: adapter, action (`settings`/`provision`/`publish`), current revision, changes; typed/versioned preflight and controlled backend changes.
- `restore_stage`: completed checkpoint ID; fresh checkpoint followed by offline restore.
- `reboot`: exact `REBOOT HOSTNAME` phrase; local policy must enable it.

Mutating jobs serialize. A checkpoint failure returns a failed job with no action exit code. Recipe and application outcomes include completed, rolled_back and rollback_failed. Discord provisioning can report partial with review required after uncertain external delivery. Terminal outcomes include completed, failed, timed_out, cancelled, interrupted and output_limited. Interrupted work is never replayed.

API action names, owner identities and scopes are shared by both interfaces. The broker does not accept a remote webhook as terminal authorization and does not expose a public unauthenticated control endpoint.
