# Reviewed backend application settings

Maestro does not guess SQL/configuration layouts or import another application's code. Each integration is enrolled locally in root-owned mode-0600 `/etc/maestro/applications.json`. Chat can edit declared application values; it cannot change credential paths, adapter kinds, executable arrays, or its owner policy.

```bash
sudo /opt/maestro/current/.venv/bin/python /opt/maestro/current/scripts/configure_applications.py
```

Use an existing Maestro target ID. The wizard reads/validates the candidate adapter before replacing the enrollment file. It does not change the application itself. Telegram then lists it under Application settings; Discord uses `/maestro application`.

## Existing Verifier 1.0.1

The `verifier-v1` adapter checks `VERSION`, SQLite schema version 1, and exact hashes of six relevant installed source files. Its validation/readiness code is bundled in Maestro from the reviewed Verifier contract. A mismatched/upgraded Verifier blocks writes until a reviewed Maestro adapter release supports it.

Enrollment requires the actual source path, database, existing Verifier environment file, Discord server ID, and initialized shared coordination database. The wizard's default paths are examples from that bot; confirm them on your VPS. This integration legitimately retains the existing Verifier module/protocol names and does not make Maestro a Rip Cars branded bot.

The Verifier must already be installed/running with its own Discord token, required intents, accessible loopback health listener and shared coordination protocol. Provider tokens and website secrets remain in protected local environment files.

### Telegram setup path

1. Select Verifier and choose Channels & member role. Set the ordinary membership role ID, such as the existing Ripper role. Staff/administrative roles are refused.
2. In Features, choose wallet/account connection, platform points, rip feed and chain webhook switches. Select the intended balance (`off`/`rpc`/`platform`), asset (`off`/`das`/`platform`) and deposit (`off`/`rpc`/`platform`) providers. Other source/mint/collection/account URL fields are typed settings.
3. Configure role rules, payment sources, texts and refresh interval. Role rules are structured JSON validated against Verifier's exact schema. Unknown fields and invalid limits fail before execution.
4. Keep Verifier paused, then review Create channels & roles. The preview lists missing resources and uses live Discord permission checks. Existing IDs are selected explicitly; name collisions are never silently adopted.
5. Review Publish panel when changing public text/buttons. Newly created buttons keep Verifier's persistent interaction IDs and are handled by Verifier.
6. Activate after readiness succeeds: ordinary role, bound rules, appropriate provider/source environment, public URL where required, selected channels, its own published panel, and live Verifier `/healthz`. Failed readiness cannot be bypassed by a direct settings write.

Discord performs the same jobs using adapter ID, action `settings`/`provision`/`publish`, and partial JSON. Example settings change:

```json
{"sync_seconds":86400,"texts":{"title":"Connect your account"}}
```

### Permissions and ownership

Provisioning uses the Verifier token, never Maestro's Discord token or your personal account. Verifier needs Manage Channels/Manage Roles and a bot role above ordinary qualifying roles. Created qualifying roles have zero permission bits. Public text channels permit ordinary members to view/history/use application commands and deny ordinary posting. The log channel denies ordinary viewing. Verifier receives View/Send/Embed/Attach/History.

Maestro joins the existing shared `server-setup` lease, renews before mutations, and records resources as owned by the Verifier bot. Pinned/foreign resources, qualifying-role administrator edits, insufficient permissions, and name collisions stop conflicting setup. Other bots retain their ownership. Settings use SQLite compare-and-swap revisions and preserve the ledger/member/application tables.

Changing deposit accounting mode after ledger records exist is blocked rather than risking duplicate points. The stored ledger is never replaced during settings rollback.

Discord API operations and SQLite commits are not a global transaction. After a partial network failure, received IDs/revisions are recorded where possible and the result is `partial` with review required. Mutations are not automatically retried after uncertain delivery. Inspect existing channels/roles before creating a new review; a resource might have been accepted remotely even when its response was lost. Maestro never deletes those resources automatically to simulate rollback.

### Update compatibility

Do not disable contract checks to force a new Verifier version. Review its schema, readiness, persistent component IDs, permissions, shared ownership and settings mutation semantics; update the bundled contract/adapter and meaningful tests in a new Maestro release. Public Discord response contracts are tested through loopback HTTP fixtures; production acceptance remains required.

## Other applications: typed JSON contract

`jsonfile` supports an existing absolute JSON object file with declared bool/int/string fields. Credential-named fields are forbidden. Example local manifest:

```json
{
  "adapters": [
    {
      "id": "my-worker",
      "kind": "jsonfile",
      "target": "my-worker-target",
      "path": "/etc/my-worker/settings.json",
      "fields": {
        "enabled": {"type": "bool"},
        "interval_seconds": {"type": "int", "min": 60, "max": 86400},
        "mode": {"type": "string", "choices": ["normal", "quiet"]}
      },
      "validate_argv": ["/opt/my-worker/venv/bin/python", "/opt/my-worker/validate.py"],
      "reload_argv": ["/usr/bin/systemctl", "reload", "my-worker.service"]
    }
  ]
}
```

These executable/path values are examples requiring real locally reviewed programs. Enrollment can be edited locally to include fixed validators/reload arrays; the wizard never asks chat to supply executable hooks. A validator must be passive, bounded and able to inspect the application's current file.

Prepare validates types, checks the current file hash, and runs the declared preflight. Execution checkpoints, rechecks the file, atomically writes with original permissions/ownership, validates the candidate, runs a declared reload, and checks target health. Failure restores the previous file only when no concurrent edit is detected, reruns its declared reload, and checks health again. An unsuccessful reload/health after rollback is `rollback_failed`, never a solved incident.

This is a configuration-file adapter, not support for arbitrary database schemas or provider APIs. Add a versioned adapter for those systems rather than guessing their storage from chat.
