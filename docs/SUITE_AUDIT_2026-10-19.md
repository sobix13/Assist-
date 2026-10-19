# Ripcars suite audit candidate — 2026-10-19

This offline audit candidate was published to the `audit/20261019` branches after review and explicit owner authorization. No VPS deployment, live Discord mutation, or mainnet transaction was performed.

The audit covers Gate, Crew, Raffle, Verifier and Maestro together, including the shared coordination registry, native setup prerequisites, reviewed settings, publication, recovery and installation. The existing gCARS Keep-current fix was already in the Gate baseline and is not counted as a new fix.

## Changes

- Verifier rejects normalized and symlink aliases between its private database and shared registry before opening either store.
- Raffle checks the validated draft and settings revisions inside the publication transaction. A racing edit or pause requires a fresh review and sends no message.
- Crew deletes tickets under the same guild setup mutex as close/reopen. Temporary slowmode restoration preserves newly bound resources and changed foreign ownership/state.
- Gate rechecks the selected resource and settings at confirmation, applies Restore-template to that resource only, and rejects stale full-setup previews. Keep-current stores the new baseline and pinned state together.
- Approved pinned Gate resources retain member filtering and explicit panel publication; automatic structural repair remains protected. Resource/integration choices paginate beyond 25 items.
- Gate acknowledges ordinary admin/member callbacks and submitted forms before database/network waits. Opening a modal still requires an initial Discord response; it must not be deferred blindly.
- Gate/Crew/Raffle release copying and Verifier staging restore the release root to 0755 after copying a private source directory. Explicit private file modes and secret exclusions are preserved.
- Maestro cannot activate Gate or bypass Crew's native live setup checks with direct database settings writes. Initial activation and changes to active Crew modules use the native panel. Pausing and editing paused modules remain available through Maestro. A full native live preflight adapter for these actions remains future work.
- Maestro's exact source contracts and test fixtures are updated together to the four audited companion commits. Unknown source edits remain rejected.

## Validation

Baseline: 934 project tests passed. Candidate: Gate 90, Crew 183, Raffle 220, Verifier 200, Maestro 263 = 956 project tests. An additional 21 compatibility tests passed using four independent processes with real temporary SQLite stores. Total: 977 passing tests, including 22 newly added regressions. Maestro's suite includes real encrypted Restic snapshot/restore checks. Cross-process tests cover all 12 ordered peer-lock combinations, initialization order, foreign/manual bindings, private/shared store boundaries, and Verifier-role consumption by Raffle.

Targeted regressions reproduced failures against the original implementations before correction. The installer copy test executes only the actual tar/chmod copy step in temporary directories, not a system installation. Audited source acceptance and rejection of unreviewed source edits were also checked.

## Operational limits

Discord API boundaries, public RPC/platform providers and webhook delivery are simulated locally. Live role order, channel access, privileged intents, HTTPS/DNS, credential validity, deposit ledger balances and production backup restore cannot be certified by this run. The four companion stores share a trusted-controller registry; it is not a security boundary against another bot process with write access to that file. External administrators and unrelated bots do not participate in its leases.

No automatic restic unlock, mandatory-checkpoint bypass, blanket resource adoption, ledger migration, or production database change is included. A repository stored only on the VPS remains vulnerable to loss of that VPS. Release versions are unchanged because these are unreleased audit candidates; a coordinated versioned release must be prepared before installation.

## Published companion source verification

The browser-published companion trees match the locally tested candidates exactly. Maestro's source-contract revision metadata points to these published commits; content hashes and runtime behavior are unchanged.

| Repository | Published audit commit |
| --- | --- |
| sobix13/Ripcars-Gate | f9e1afaa10fbfad3aab343ab8ea17f8cc64fdf63 |
| sobix13/Ripcars-Crew | fbf64123dab64121ae8be118f17fd2de10e8e0bd |
| sobix13/Ripcars-Raffle | 217e1d2cff5bf526abffeeba18a5c790300e6563 |
| sobix13/Verify-bot | 03d50bd4360ce632eb2ccf3f239b215e97659177 |

The main branches and VPS services were not changed during publication. A push is not an installation.

## Backup timestamp follow-up

Raffle formerly read the seconds and nanoseconds separately when naming a backup. Crossing a second between those reads could give the newest file an older name, so retention deleted it immediately. The filename now uses one clock reading. A deterministic regression fails on the old code and passes on the fix. Raffle validation: 221 tests passed. Maestro source hashes and the matching fixture were updated together; source validation remains enforced. The original audit counts above describe the initial audit run, before this follow-up.

VPS update recovery must inspect the full traceback and already completed application steps before retrying. A prepared Verifier release without an active current symlink is a first-install task, not an existing installation to update. Never skip backup verification or tests to finish an update.
