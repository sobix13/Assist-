# Quiet launch update

Updates Gate to 1.1.0, Crew to 1.1.0 and Maestro to 1.1.4. Raffle code, settings and role-only eligibility are unchanged. Verifier is intentionally stopped and disabled; its installation, credentials, wallet links, roles and data remain available for later use.

## Operator flow

1. Run the pinned quiet-launch installer as root using a detached systemd service.
2. Wait for `QUIET_LAUNCH_UPDATE_COMPLETED`.
3. In Discord open `/gate panel > Features on / off`. Turn Questions off for CAPTCHA-only entry. Existing feature settings are retained until you change them.
4. Open `/crew panel > Channel rules`, choose a channel and toggle allowed content. Advanced controls remain under More settings.
5. Move and hide unused channels manually in Discord. The update does not rearrange channels or change your launch layout.

All Gate/Crew public and admin text remains English. Maestro's existing private interface language is retained.

## Installation behavior

`scripts/update_quiet_launch.py --gate SOURCE --crew SOURCE` stages all three code releases before changing current links. It requires unchanged dependency files and reuses each installed virtual environment; it downloads no Python packages. Keep previous releases because the reused environment lives there.

Gate and Crew run their full tests before activation. Maestro receives compilation/import checks on the VPS; its complete tests are run on the reviewed source before publication. The existing verified Maestro checkpoint runs before code activation. Its configured writer-pause policy still applies. A failed checkpoint leaves current companion code in place and resumes owner interfaces.

A root-only backup includes consistent SQLite copies, environment files, Maestro configuration, previous release paths and service state. If activation fails, code links and changed Verifier monitoring/service settings are restored. Application data is never rolled back to an older database.

Only Verifier's Maestro targets are disabled, with expected-running and automatic restart disabled as well. Notification planning suppresses intentionally disabled target alerts, without marking incidents healthy. Re-enable its service and monitoring deliberately when the feature returns.

## Validation boundary

Tests use real Discord SDK components, mocked Discord HTTP boundaries, temporary SQLite databases, a separate-process companion coordination suite, and real Restic tests where required. No private production token is used. New UI interactions still require a short live Discord check after deployment. This repository publication does not itself deploy the VPS.

Reviewed validation: Gate 99 tests, Crew 204 tests, Maestro 276 tests, and 21 companion integration tests passed (600 total). Production Discord interactions were not executed in this environment.
