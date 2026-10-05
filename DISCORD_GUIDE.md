# Maestro in Discord

Run `/maestro panel` in the locally enrolled server. The superadmin's numeric Discord user ID is configured independently from Telegram ID 313342234. Discord Administrator does not make somebody a Maestro superadmin.

## Panel

The dropdown groups eleven sections: Setup & schedules, Services & health, Host inventory, Recovery & diagnostics, Terminal, Files & code, Jobs & output, Backups & restore, Application settings, People & access, and Guide. Only the selected section's actions are shown.

Bind a private report channel in Setup. Channel selection remains in the draft until Save. A channel with an ordinary role/member allow is rejected. Guild administrators can see private channels by Discord design; full terminal/download output uses private owner delivery or ephemeral interactions.

The optional Create private channel button needs Manage Channels. Basic reporting needs View Channel, Send Messages, Read Message History, Attach Files and Embed Links. No privileged member/message-content intent is needed for Maestro.

## Command groups

| Subcommands under `/maestro` | Purpose |
|---|---|
| `panel`, `health`, `guide` | Structured UI, backend status, instructions |
| `inventory`, `check`, `logs` | Passive discovery, checks, bounded logs |
| `service`, `container` | Reviewed service/container actions |
| `terminal`, `upload`, `run_file` | Reviewed code and staged file execution |
| `job`, `output` | State, stdin/EOF/cancel, full retained output |
| `checkpoint`, `checkpoints`, `restore`, `package` | Verified snapshots, offline restore, categorized ZIPs |
| `backup`, `export` | Encrypted Maestro-state download, redacted operational export |
| `recipes`, `repair` | Locally declared deterministic recipes |
| `application` | Typed application settings/provision/publication review |
| `access` | Expiring observer/operator grant or revoke |

There are 23 subcommands. Private views/modals and the broker independently recheck the actor; a stolen button is not authorization. Service lists page beyond Discord's 25-option limit.

## Shared work without conflicting confirmations

The Telegram and Discord owner identities can share job/upload history when explicitly linked during local enrollment. A Telegram review can be confirmed only by its initiating Telegram actor; a Discord review remains bound to its Discord actor. Both send mutations through one serialized broker queue.

Observers/operators get an assigned-services panel rather than the full admin panel. Only operators can review basic service actions for those services. Delegates cannot run code, edit applications, download backups or grant access.

Application commands use adapter ID, `settings`/`provision`/`publish`, and a partial JSON change. Verifier activation is a `settings` change such as `{"enabled":true}` and must pass readiness. Telegram exposes additional grouped buttons for these same backend actions; see [APPLICATIONS.md](APPLICATIONS.md).

Results remain in the shared owner job history even if DMs are disabled. Use `/maestro output` from the private interface to retrieve the retained redacted log. After starting, complete [ACCEPTANCE.md](ACCEPTANCE.md) on the real server.
