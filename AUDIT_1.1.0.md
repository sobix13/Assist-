# Maestro 1.1.0 change review

Scope: the owner-operated Maestro broker, Telegram and Discord interfaces, and the owned Rip Cars suite. Expected use is one VPS with linked owner interfaces and explicitly scoped delegates. Monitoring may cover hundreds of services; each application remains a separate controller.

## Installer correction in 1.1.1

The owner's VPS exposed a test-fixture defect that clean CI hosts did not reproduce: package tests inherited the installed Crew source path, while fake probe setup left the inventory object using its original host runner. Consequently a fake snapshot could not contain a real `SUITE_TEST_RESULTS.txt` and daily-monitor tests ran slow host discovery. The failed staging step did not activate the candidate release.

Test engines now use temporary configuration, inventory and database roots, with private environment loading disabled and production target paths cleared on fresh fixture creation. Reopening a fixture preserves its explicit target edits. Inventory commands use the fake probe runner when selected. Native Restic integration tests still execute real encrypted snapshots and restores against their own fixtures. Two regressions cover an installed Crew source marker and attempted host-command escape. Runtime backup coverage and production settings are unchanged.

## Confirmed defects and fixes

1. Original Discord response helpers forwarded `view=None` to sends. The shared response helper now removes absent views, preserving edit-message removal semantics.
2. The old environment reader rejected valid lowercase service keys and stopped discovery. Valid variable names and surrounding key whitespace are accepted. Invalid-line diagnostics identify the path and line without echoing the value.
3. Original automatic delivery replayed transitions and uploaded job files. Essential delivery reads current incidents, acknowledges routine backlog separately for each owner, keeps output on request, and persists delivery state before acknowledgment.
4. Constant incidents and short worker flaps produced repeated messages. Constant failures announce once, workers need ten minutes of continuous failure, recovery needs ten clear minutes, and familiar setup reviews appear in the daily summary. Serious database escalation produces a fresh alert instead of remaining hidden by the earlier issue's key.
5. Notification options were frontend-only and too coarse. Six broker settings now cover each transport and four message categories. They survive restarts and apply to both frontends. Muting transport drains its queued messages without stopping monitoring or commands.
6. A SQLite progress deadline was labelled database corruption/failure. Deadline interruption now produces an inconclusive review and still blocks automatic recovery until a conclusive check. The broker's own larger database receives a fifteen-second budget.
7. Unchanged minute-by-minute snapshots expanded the broker database unnecessarily. Watch snapshots now record state/issue transitions plus an hourly sample. Full hourly and daily reports remain. A target/kind/time index supports history queries.
8. A concurrent manual monitoring cycle caused scheduler errors. Scheduled checks defer while a cycle owns the monitoring lock. Cross-process backup locks are reported as deferred, without claiming an integrity check happened.
9. Checkpoint errors discarded the Restic cause and left only `Denied`. The redacted command detail now survives in the job and daily report. A read-only repository check is available without starting a mutation job.
10. A successful `systemctl restart` exit was reported as a completed operation even when application health failed. Start/restart of an enrolled target now includes its service probes; a failed postcheck is a failed job with diagnostic evidence.

## Operator interface

- A small home screen leads to Server or Discord bots. Jobs and help are immediately available.
- Advanced menus and all original commands remain available.
- Gate, Crew, Waffle and Verifier have separate dashboards and per-field help.
- Labels, typed choices and stepwise input replace JSON in routine suite configuration.
- Existing questions, role rules and payment sources have edit/remove controls; role/question switches preserve their identifiers and existing state.
- Both interfaces use the same workflows and input parsers. Discord uses actual SDK buttons/selects/modals. Telegram navigation edits its current panel; background reports remain separate messages using task-local context.
- Previews are computed from validated changes, with unrelated nested settings omitted from the operator summary.
- Errors and job stages have plain-language explanations. Full output remains downloadable.

## Integration boundaries

Gate 1.0.2, Crew 1.0.1 and Waffle 1.0.1 are pinned by Git blob hashes for all runtime Python files. Verifier retains its existing 1.0.1 SHA-256 contract. The fixture ZIP stores the exact pinned suite source for reproducible compatibility tests.

Standard installed paths are recognized without creating databases or adopting Discord objects. Custom paths still require protected local enrollment. Settings use the native schemas and validators, revision checks, SQL transactions and native history tables. Only the requested application's settings change. Verifier retains live permission, ownership, ledger-mode and publication preflight checks.

Waffle creation stages a draft. Publishing is a distinct reviewed operation. The one-shot publisher calls the native Waffle service with the same identity, publication lock, journal, ownership checks and shared coordination registry. It has no command synchronization, scheduler or backup loop. On a root broker it runs as the database owner's OS identity. The primary Waffle service continues handling buttons and deadlines.

Gate and Crew public-message publication remains in their native Discord panels and is described honestly in the UI. This update does not transfer their resource ownership or overwrite manually pinned roles.

## Connection input handling

Only a fixed allowlist of Verifier connection fields is editable. Bot tokens, environment paths and arbitrary variable names are not editable through this flow. HTTPS endpoint validation, private-file checks and environment revisions apply. Secrets are encrypted with a private per-agent Fernet key before entering pending/job payloads. They are not returned in status, previews, event output or audit messages. Confirmed values are written atomically to the already-enrolled private environment file. Restart is a separate reviewed action. Private chat input remains subject to Telegram's or Discord's own message retention.

The existing locally configured root runner, delegate scope, immutable owners, review expiry, single-use confirmation, mutation queue and mandatory checkpoint policy remain enforced.

## Verification

`run_tests.sh` runs the complete unittest suite, Python compilation and shell syntax checks. New integration tests exercise live loopback Telegram HTTP, real Discord SDK layouts, cross-bot isolation, native SQLite schemas, conflicting revisions, secret redaction/encryption, rejected input, notification persistence/muting, and failed-backup blocking.

A child interpreter runs the pinned native Waffle publisher with the upstream Discord SDK fixtures. It verifies real publication state, message ownership, duplicate rejection and ambiguous-delivery journaling. Existing live Discord REST fixtures verify Verifier resource preflight and mutations. Restic acceptance tests use the official Restic 0.16.4 standalone binary after its published SHA-256 was checked; they create encrypted repositories and verify snapshot/export restore.

See `VALIDATION.json` for the final executed test count and package checks.

## Live prerequisites and remaining unknowns

No VPS or live Discord/Telegram account was used during these tests. The source update does not prove the current server is healthy. The actual VPS repository-integrity failure requires its redacted Restic diagnostic before any targeted repair. No backup was deleted or protection bypassed.

Melee Zone's Discord privileged-intent approval, domain/DNS/TLS routing, team-supplied API credentials, and correct production contract/collection/treasury addresses require their actual providers or administrators. Current failures of unrelated host services cannot be repaired from an old report alone. Read-only diagnostics and native ownership reviews remain available. No unrelated service was restarted or adopted by this code review.
