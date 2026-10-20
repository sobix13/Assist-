# Role raffle upgrade: Maestro 1.1.2

This adapter release matches Raffle 1.1.0 and Verifier 1.1.0. It pins source hashes, preserves draft revision checks in the native publisher and uses selected role ticket presets for broker-created drafts. New drafts using the shared entry role require result approval by default.

The easiest everyday administration is the native `/raffle panel`: New raffle, Current raffle, Past raffles, Settings. Verifier is independent: wallet qualifications use a 12-hour qualified-member cycle; platform points update initially and then only through an admin-requested batch. Raffle reads neither source. Advanced token/RPC onboarding changes are a later phase.

## Existing installation

Use exact reviewed Git commits for all three source checkouts. Then run as root:

```sh
python3 /path/to/maestro/scripts/update_role_raffles.py --raffle /path/to/raffle --verifier /path/to/verifier
```

Use `--check-only` to validate source versions, source pins and the common coordination protocol without changing services. The real update requires the five existing Raffle, Verifier and Maestro units to be active. It does not reinstall Gate/Crew. The existing backup policy may briefly pause and resume configured writers during checkpoint verification.

All three releases are staged in physical `/opt/.../releases` directories and tested before any interface stops. Runtime users must be able to read and execute the staged code. Existing configuration is retained. Frontends stop before the verified Maestro checkpoint to prevent new owner commands. A failed checkpoint restores their availability and stops the update. No lock or backup integrity check is bypassed.

After stopping the five units, root-only local SQLite copies are checked for integrity, including committed WAL data; unit files, configuration and prior code paths are retained. Failed readiness switches all three code links and unit files back, while preserving current application data.

After `ROLE_RAFFLE_UPDATE_COMPLETED`, open `/raffle panel`, save Quick setup and check the fixed entry role hierarchy. Run a small test raffle in review mode and one in automatic mode. Check native manual grant registration, public counts, result publication, role removal and archive retention. Open `/verifier panel` for provider setup and admin point-batch controls. Server readiness does not prove these live interactions.
