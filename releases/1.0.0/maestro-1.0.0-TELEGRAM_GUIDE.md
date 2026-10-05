# Maestro in Telegram

Maestro operates in private chats. The locally enrolled superadmin defaults to user ID 313342234. A second user's access exists only after the superadmin grants an expiring scope. A group, username, or forwarded message never grants authority.

## First use

Send `/start`, then choose Start setup wizard. Five stages explain the identity, inventory, backup coverage, schedule, and activation. The VPS enrollment wizard supplies bot credentials and protected backend paths once; the chat wizard controls ordinary operations. Finishing enables monitoring and leaves automatic recovery off.

Home cancels the current input step. `/cancel` clears a pending input workflow. Ordinary messages are never executed unless you explicitly opened a terminal/file input step and then confirmed the preview.

## Home sections

| Section | Actions |
|---|---|
| Setup & schedules | Wizard, health, monitoring pause/activate, schedule settings |
| Services & health | Paged service cards, Check, Logs, Start/Stop/Restart, monitoring fields, per-service ZIP |
| Host inventory | Services, containers, databases, repositories, ports, timers, saved PM2 declarations |
| Recovery & diagnostics | Known diagnostics, fixed recipes, maintenance, deliberate recovery opt-in |
| Terminal | Runner or root Bash input, exact review, confirmation, output |
| Files & code | Stage a document/archive, inspect manifest, run an entry, attach files to code |
| Jobs & output | Shared owner history, states, downloads, stdin/EOF, cancel |
| Backups & restore | Coverage, checkpoint, classified ZIP downloads, checkpoint list, report export |
| Application settings | Versioned Verifier controls and locally enrolled typed JSON fields |
| People & access | Observer/operator grant wizard, expiry, assigned target IDs, revoke |
| Guide | Contextual help and backend health |

Menus expose the selected branch and Home/Guide. Long settings, logs, request previews, and output become downloadable files instead of overflowing a Telegram message.

## Services appear automatically

Inventory refreshes every five minutes and gives newly discovered loaded services/containers stable cards. It does not overwrite your edited paths, recovery flags, or adapter settings. Cards are paged in groups of ten. A stopped service remains selectable; disappeared services are retained for review rather than silently deleted.

Opening a systemd card passively reads its working directory/environment-file declarations and fills only previously blank install/env/database fields. It then shows a check result. Confirm those inferred paths before using backups or application adapters. It never executes application code during discovery.

Check is passive. A Start/Stop/Restart click opens a review; it does not execute immediately. Monitoring/recovery flags and health endpoints are changed through Edit monitoring using partial JSON. The current revision guards against overwriting another session's edit.

## Terminal and files

1. Choose Terminal, then the locally permitted profile.
2. Paste plain Bash code. The exact payload, profile, deadline and working directory are reviewed.
3. Confirm within five minutes. The broker serializes execution behind any other mutation and requires a verified checkpoint.
4. Open the returned job or wait for private result delivery. Use Output for the complete retained log, stdin/EOF for an active interactive process, or Cancel to stop its process group.

To run files, send a document in the private chat, inspect the content-hash manifest, and choose Run entry file. Example input:

```json
{"entry":"main.py","runtime":"python","profile":"runner","args":[],"timeout":120}
```

Uploading/inspecting does not execute it. ZIP/TAR archives reject traversal, links, duplicate paths and oversized expansion. Runner is a separate non-root account without sudo; it is not a container. Root never substitutes for a failed runner launch.

Inputs are limited to 8 MiB uploads, 32 MiB expanded archives, and 200 entries. Output retention is 32 MiB by default and adjustable from 1 to 64 MiB. Exceeding retention stops the process and explicitly marks the output incomplete.

## Verifier controls

Application settings separates Features, Channels & member role, Texts, Role rules, Payment sources, Refresh interval, Provision, Publish, and Activate/Pause. Boolean/provider choices are buttons; structured fields accept typed values or JSON. Only locally enrolled adapters appear.

Each application mutation validates against the installed version/schema, reads live Discord permissions where applicable, shows a review, creates a checkpoint, and checks the result. A changed settings revision blocks the old review. Provision/publication use the Verifier bot's existing identity and its shared lease, not the Maestro Discord token. See [APPLICATIONS.md](APPLICATIONS.md).

## Downloads and recovery

Backups & restore creates a verified filesystem checkpoint or categorized ZIP packages. All packages produces a database ZIP, per-application source ZIPs for declared install directories, and a Maestro reports ZIP. Per-service backup includes that service's recognized consistent database exports and selected source.

ZIPs are private plaintext downloads containing application data. Full Restic checkpoints remain encrypted. A ZIP does not include every environment/credential file or every unknown database; review the manifest and [BACKUPS.md](BACKUPS.md). Large downloads use numbered binary parts with checksums; join them in exact numeric order before opening.

`/restore CHECKPOINT_ID` stages a verified full restore into a new private folder without overwriting production. `/maestro_backup PASSWORD` produces an encrypted Maestro-state download; prefer a unique password of at least 12 characters and remove sensitive command text from your chat when appropriate.

## People

Grant access is four stages: numeric identity, observer/operator, service IDs, expiry hours. Use `telegram:USER_ID` or `discord:USER_ID`. Revoke is available beside an active grant. Renew/change a grant through the broker with its current revision, or revoke and grant again through the simple panel.

Delegates have their own chat/input state and only My services, My jobs, and Guide. They receive no global owner report feed. Backend authorization is checked on every action, including when a revoked delegate still has old buttons visible.
