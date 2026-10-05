from __future__ import annotations

import asyncio
import json
import os
import socket
import ssl
import struct
from contextvars import ContextVar
from pathlib import Path

from .security import Denied

MAX_MESSAGE = 12 * 1024 * 1024


class AgentServer:
    def __init__(self, engine, path, gid=None, tls=None):
        self.engine = engine
        self.path = Path(path)
        self.gid = gid
        self.server = None
        self.tls = tls

    async def start(self):
        if self.tls:
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.load_cert_chain(self.tls["cert"], self.tls["key"])
            context.load_verify_locations(self.tls["ca"])
            context.verify_mode = ssl.CERT_REQUIRED
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.server = await asyncio.start_server(self.handle, "127.0.0.1", self.tls.get("port", 0), ssl=context,
                         limit=MAX_MESSAGE + 1, ssl_handshake_timeout=5)
            self.port = self.server.sockets[0].getsockname()[1]
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        if self.path.exists() or self.path.is_symlink():
            if not self.path.is_socket() or self.path.is_symlink():
                raise Denied("Refusing to replace a non-socket API path.")
            self.path.unlink()
        self.server = await asyncio.start_unix_server(self.handle, path=str(self.path), limit=MAX_MESSAGE + 1)
        self.path.chmod(0o660)
        if self.gid is not None:
            os.chown(self.path, 0, self.gid)
        return self

    async def handle(self, reader, writer):
        try:
            sock = writer.get_extra_info("socket")
            if not self.tls:
                _, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
            raw = await asyncio.wait_for(reader.readline(), 10)
            if not raw or len(raw) > MAX_MESSAGE:
                raise Denied("Request size rejected.")
            request = json.loads(raw)
            if self.tls:
                certificate = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
                actor = self.engine.policy.authenticate_certificate(certificate, request.get("token"), request.get("actor"))
            else:
                actor = self.engine.policy.authenticate(uid, request.get("token"), request.get("actor"))
            result = await asyncio.wait_for(self.engine.dispatch(actor, request.get("action"), request.get("params", {})), 300)
            response = {"ok": True, "result": result}
        except (ValueError, KeyError, TypeError, asyncio.TimeoutError) as exc:
            response = {"ok": False, "error": self.engine.redactor.clean(exc)[:500] or type(exc).__name__}
        except Exception as exc:
            # Do not disclose local paths, credentials or tracebacks to a rejected connection.
            response = {"ok": False, "error": "Backend operation failed: " + type(exc).__name__}
        try:
            writer.write(json.dumps(response, ensure_ascii=True).encode() + b"\n")
            await asyncio.wait_for(writer.drain(), 15)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if not self.tls and self.path.is_socket():
            self.path.unlink()


class Client:
    def __init__(self, path, token, actor, tls=None):
        self.path, self.token, self.actor = str(path), token, actor
        self.tls = tls

    async def call(self, action, **params):
        if self.tls:
            context = ssl.create_default_context(cafile=self.tls["ca"])
            context.load_cert_chain(self.tls["cert"], self.tls["key"])
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", int(self.tls["port"]), ssl=context, server_hostname="localhost", limit=MAX_MESSAGE + 1), 10)
        else:
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(self.path, limit=MAX_MESSAGE + 1), 5)
        try:
            request = {"token": self.token, "actor": self.actor, "action": action, "params": params}
            encoded = json.dumps(request, ensure_ascii=True).encode() + b"\n"
            if len(encoded) > MAX_MESSAGE:
                raise Denied("Request exceeds the local API size limit.")
            writer.write(encoded)
            await asyncio.wait_for(writer.drain(), 15)
            raw = await asyncio.wait_for(reader.readline(), 305)
            if not raw:
                raise ConnectionError("Backend disconnected. Reopen the panel and check job history before retrying.")
            response = json.loads(raw)
            if not response["ok"]:
                raise Denied(response["error"])
            return response["result"]
        finally:
            writer.close()
            await writer.wait_closed()


class ActorClient:
    """Each Discord interaction binds its own actor without changing shared state."""
    def __init__(self, client):
        self.client = client
        self.actor_context = ContextVar('maestro_actor', default=client.actor)

    def bind(self, actor):
        self.actor_context.set(actor)

    async def call(self, action, **params):
        c = self.client
        return await Client(c.path, c.token, self.actor_context.get(), tls=c.tls).call(action, **params)
