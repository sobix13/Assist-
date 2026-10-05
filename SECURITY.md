# Security and operational boundaries

This is an owner-operated remote terminal bridge. Root jobs have genuine root power. Enrolled code and filesystem coverage must reflect that intended trust boundary.

## Authentication

- Locally enrolled numeric owner identities; no Discord role/Administrator or Telegram username grants access.
- Discord additionally requires the configured guild. Views, modals and slash commands recheck ownership.
- Telegram accepts the enrolled superadmin or explicitly scoped delegates, each in their own private chat with matching numeric sender ID. Groups, unrelated users and forged cross-owner callbacks are rejected.
- Separate unprivileged Unix accounts and credentials for the two interfaces. Default broker access checks Unix peer UID, token and transport-scoped actor.
- Optional loopback mutual TLS additionally requires a pinned client certificate. The broker is not a public HTTP terminal service.
- Owner identity links are deliberate, local and disjoint. Linked identities share jobs/uploads, but cannot confirm each other's transport-bound review tokens.
- Local policy and environment files are root-owned 0600; owners/privilege/credential changes are not ordinary chat settings.

A compromised trusted frontend can assert its locally enrolled actor to the broker. Bot tokens, frontend credentials, owner chat accounts and host access therefore belong to the administrative trust boundary. Protect owner accounts with strong authentication and keep this bot out of shared operator accounts. The restricted UI does not make a compromised root terminal harmless.

## Execution

Exact reviewed requests use single-use expiring tokens and digests. Upload metadata/content hashes are checked again. Requests are consumed before execution; restarts do not replay them. Mutating jobs serialize across both interfaces, and repository operations also use a process-level lock.

A fresh verified checkpoint is required by default. Only the local root policy can override that requirement; the public enrollment wizard always enables it. A failed/partial checkpoint never falls back to execution without backup. Read-only fixed diagnostics and staging are separate from owner code execution.

The runner is a non-root account without sudo privileges, not an isolated container. It receives only its writable job copy and a clean environment. Root execution is explicitly enrolled. A failed runner setup never switches to root. Timeouts, output caps and cancellation kill process groups, including children.

Uploads are capped at 8 MiB; archives at 32 MiB expanded and 200 entries. Traversal, symlinks, hard links, device entries and duplicate file paths are rejected. Staging/inspection does not execute files.

## Sensitive state

Pending/job source payloads are stored in the root-private SQLite database. Raw output is written only into root-private spools; frontends never serve raw files. Known secrets and common credential patterns are redacted over accumulated output before delivery, including split process chunks. This is not a guarantee that arbitrary unknown secrets in owner output can all be recognized.

Ordinary exports omit job source and enrollment credentials. Encrypted `.rca` downloads include source payloads inside encrypted Maestro state; keep their password separate. Restic snapshots may include sensitive application files and are encrypted; protect repository credentials and off-host access policies.

Discord reports require a private text channel with explicit non-owner/non-administrator access rejected. Discord guild administrators can still see channels by platform design. Detailed terminal output uses private owner delivery or ephemeral interaction downloads. Telegram uses only the owner's private chat. Disabling owner DMs does not erase stored output.

## Recovery boundaries

No deletion of foreign Discord roles/channels, no replay of member/business operations, passive monitoring does not write shared resource ownership. The explicitly enrolled Verifier provisioning adapter is the narrow exception: it participates in the existing setup lease and records only resources owned by that Verifier identity. Existing recovery controllers retain ownership when a target is `external`. Active suite setup leases defer restarts.

Fixed recipes are root-owned local configuration, not AI-generated fixes. Rollback accepts specific code/configuration roots; it rejects database content before capture and again before deletion. Remote transactions and changes outside those roots require their own compensating plan. Offline restore is explicit and never overlays production automatically.

An on-host assistant cannot observe or repair a fully offline VPS. The optional second-host witness observes a chosen TCP listener only. Host-loss recovery still requires the VPS provider/console and an available off-host backup.


## Delegates, adapters and downloadable ZIPs

Observers/operators are numeric transport identities with expiring service scopes. Backend access is rechecked per action and again before/after the execution checkpoint. Revocation consumes pending reviews and cancels active/queued delegated jobs. Delegates have no terminal, credential, backup, application-settings or onward-grant capability. A remotely accepted side effect is not automatically undone by revocation.

Application adapters are enrolled locally in a root-private manifest. Verifier source/schema pins, settings revisions, role hierarchy, effective permissions, resource ownership and shared leases guard its mutations. Generic JSON adapters expose declared typed fields and fixed executable arrays only. Failed changes restore settings only when a concurrent edit has not occurred; a failed rollback verification is escalated.

Categorized ZIP downloads are private plaintext, can contain member/application data, and are restricted to the owner-linked identities. Their source filter excludes credential-named/environment files; it cannot prove no secret was hard-coded inside source or stored inside a database. Treat ZIPs as sensitive. Encrypted full Restic checkpoints have separate credential/capacity/retention requirements.
