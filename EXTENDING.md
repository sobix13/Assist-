# Extend the assistant

## Other VPS applications

Inventory identifies resources without taking ownership. Add the actual unit/driver in Services & health. Review exact paths and recovery ownership, then enable monitoring. A web worker can use a readiness route; a scheduled job can update a successful-work heartbeat after completion with an appropriate max age. A cron filename or active systemd process is not sufficient proof of business success.

For PM2, this version reads saved declarations without spawning a daemon. Enroll a real systemd wrapper or useful-work heartbeat; a live PM2 control adapter is not implemented. Docker State/logs and enrolled start/stop/restart are supported; volume consistency belongs in backup coverage.

New repositories can be assigned to individual targets for daily change observations. Changes are reported, not pulled or installed automatically.

## Fixed repair recipes

Create `/etc/maestro/recipes.json` locally, root-owned 0600. Start with manual recipes. Example for a reviewed nginx reload on a host that has these exact paths and a monitored `nginx` target:

```json
{
  "recipes": [
    {
      "id": "nginx-reload",
      "target": "nginx",
      "preflight": ["/usr/sbin/nginx", "-t"],
      "argv": ["/usr/bin/systemctl", "reload", "nginx.service"],
      "rollback_paths": ["/etc/nginx/sites-enabled"],
      "rollback_argv": ["/usr/bin/systemctl", "reload", "nginx.service"],
      "automatic": false,
      "match_codes": [],
      "timeout": 90,
      "verify_seconds": 20
    }
  ]
}
```

Paths under `sites-enabled` may be symlinks; preserving a link does not undo changes to its target elsewhere. Enroll each real configuration path affected by the recipe. Do not include overlapping roots or application data directories. The example is a template, not a default auto-fix for your VPS.

Fixed arrays use absolute executables and no injected chat substitutions. Review actual effects, credentials, health signal and compensating action. A recipe must be narrow enough to verify and roll back. Add PostgreSQL/MySQL/Redis consistent exports separately before enabling any mutation of those workloads.

Automatic recipes additionally need `automatic: true`, explicit `match_codes`, the target's `auto_recipe` ID, global recovery, repeated evidence and budgets. Dangerous/unresolved credential, permission, database, dependency, disk and port evidence still blocks unattended restart/repair. Unknown failures are escalated.

The current rules recognize permissions, invalid tokens, port conflicts, corrupt/busy databases, missing dependencies, provider failures, disk full, DNS/TLS failures, memory pressure, start limits and Telegram polling conflicts. Additional signatures are added to `runbooks.py` through a tested code release; recipes can expand actions without embedding an AI dependency.

## New health/store adapters

`Probes` owns passive health, `Checkpoints` owns consistent exports, and `RecipeBook` owns fixed remediation. Add an adapter with explicit deadlines, bounded output, read-only observations, clear failure classifications and fault tests. Do not import an unknown application's code as a health probe.

Provider snapshots, distributed stores, live PM2 control and application-specific business tests require dedicated adapters. The existing architecture supplies enrollment, authorization, scheduling, backup gates, output delivery and operation records; it does not imply an adapter already exists for every technology.

## Existing Rip Cars suite

The shared coordination protocol source matches all four packaged bots. Passive monitoring reads registry health/leases. The enrolled Verifier adapter can create its own missing resources using its own identity and lease; it never takes ownership from another bot. Each application keeps its own state, responsibilities and restart decision. FUTARCHIST and Melee presets supplement this suite without changing their code.


## Versioned application settings

See APPLICATIONS.md for the bundled Verifier 1.0.1 contract and the declared JSON-file adapter. Each new database/provider integration needs an explicit schema/API contract, compare-and-swap changes, precise permission/ownership checks, postcheck and fault tests. Never infer an arbitrary SQL layout and generate writes from a chat field. Keep provider secrets locally enrolled.
