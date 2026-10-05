# Optional pinned mutual TLS on loopback

Default Ubuntu deployment uses the permission-restricted Unix socket. A restricted runtime that cannot create Unix sockets can use newline-delimited JSON over mutual TLS on `127.0.0.1`. It remains a private authenticated broker, not a public HTTP endpoint.

For both interfaces, generate distinct client credentials and enroll each fingerprint separately:

```bash
sudo /opt/maestro/current/.venv/bin/python /opt/maestro/current/scripts/create_tls.py /etc/maestro-tls-discord
sudo /opt/maestro/current/.venv/bin/python /opt/maestro/current/scripts/create_tls.py /etc/maestro-tls-telegram
sudo sh -c 'cat /etc/maestro-tls-discord/ca.pem /etc/maestro-tls-telegram/ca.pem > /etc/maestro-tls-discord/trusted-clients.pem'
```

The helper prints each client certificate fingerprint, never a private key. Add the matching `certificate_sha256` to each root policy frontend entry, retaining its own transport/token/UID. The first generated server certificate is used by the broker; its CA validates the server for both clients. The combined CA file lets the broker validate both separately signed clients. The second generated server credential is unused. The helpers do not persist CA signing keys; rotate credentials/trust together before 90-day leaf expiry.

Choose a free loopback port, then create an agent service override:

```ini
[Service]
ExecStart=
ExecStart=/opt/maestro/current/.venv/bin/python -m maestro.agent --tls-port 8921 --tls-cert /etc/maestro-tls-discord/server.pem --tls-key /etc/maestro-tls-discord/server.key --tls-ca /etc/maestro-tls-discord/trusted-clients.pem
```

In `/etc/maestro/frontend.env`:

```text
AGENT_TLS_PORT=8921
AGENT_TLS_CERT=/etc/maestro-tls-discord/client.pem
AGENT_TLS_KEY=/etc/maestro-tls-discord/client.key
AGENT_TLS_CA=/etc/maestro-tls-discord/ca.pem
```

In `/etc/maestro/telegram.env`:

```text
AGENT_TLS_PORT=8921
AGENT_TLS_CERT=/etc/maestro-tls-telegram/client.pem
AGENT_TLS_KEY=/etc/maestro-tls-telegram/client.key
AGENT_TLS_CA=/etc/maestro-tls-discord/ca.pem
```

Keep certificates/CA root-owned and frontend-nonwritable. Give each private client key read access only to its own interface account. The hardened unit makes `/etc` read-only during execution:

```bash
sudo chmod 0755 /etc/maestro-tls-discord /etc/maestro-tls-telegram
sudo chmod 0644 /etc/maestro-tls-discord/*.pem /etc/maestro-tls-telegram/*.pem
sudo chown maestro:maestro /etc/maestro-tls-discord/client.key
sudo chown maestro-telegram:maestro /etc/maestro-tls-telegram/client.key
sudo chmod 0400 /etc/maestro-tls-discord/client.key /etc/maestro-tls-telegram/client.key
sudo chmod 0600 /etc/maestro-tls-discord/server.key /etc/maestro-tls-telegram/server.key
sudo systemctl daemon-reload
sudo systemctl restart maestro-agent.service maestro-discord.service maestro-telegram.service
```

Chain validation without the enrolled client fingerprint fails authentication. A correct certificate with the wrong transport credential also fails. No automatic downgrade occurs. On-host systemd notifications still use Unix datagrams; a runtime without systemd is only a local test fixture, not a substitute production supervisor.
