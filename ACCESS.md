# Personal access model

Superadmin identities live in `/etc/maestro/policy.json`, enrolled directly on the VPS. Telegram defaults to `telegram:313342234`; the Discord identity is separately supplied. The enrollment wizard refuses owner replacement. Chat grants cannot create, remove or replace a superadmin.

| Capability | Superadmin | Observer | Operator |
|---|---|---|---|
| Assigned target health/logs | All | Assigned only | Assigned only |
| Own reviewed job history/output | Yes | Yes | Yes |
| Reviewed start/stop/restart | Yes | No | Assigned only |
| Terminal/files/stdin | Local policy | No | No |
| Backups/application settings | Yes | No | No |
| Global inventory/configuration | Yes | No | No |
| Grant/revoke/renew access | Yes | No | No |
| Control Maestro's broker/interfaces | Local policy | No | No |

Grants name one to 100 distinct existing target IDs and expire after 60 seconds to one year. The simple UI accepts one to 8,760 hours. Use a numeric transport identity: `telegram:USER_ID` or `discord:USER_ID`. Usernames and Discord role IDs are not accepted.

Grants have a revision; concurrent updates fail. The simple UI can revoke then grant again to renew/change scope. The broker supports updating with the current revision. Revocation deletes the grant, consumes pending reviews, and requests cancellation of queued/active delegated jobs. An already accepted external effect cannot always be undone by revocation.

Telegram sessions are separated per numeric delegate, private-only, and every action rechecks broker access. Discord actor context is isolated per interaction/task. Delegates never inherit the superadmin's client actor. Old visible buttons do not bypass expiry/revocation.

The two superadmin identities may be explicitly linked for shared jobs/uploads. Confirmation still requires the initiating actor. Delegates are not linked to owners or to one another and receive no global report feed.

The trusted frontend can assert enrolled actors of its own transport to the broker. Keep bot/frontend tokens and your chat account secure. The local root broker must have sufficient VPS privilege for the operations you choose; a UI restriction is not a sandbox around root.
