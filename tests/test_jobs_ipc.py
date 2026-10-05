import asyncio
import base64
import os
import socket
import sys

from maestro.ipc import AgentServer, Client
from maestro.process import MAX_OUTPUT
from maestro.security import Denied
from scripts.create_tls import generate
from .helpers import EngineCase, OTHER, OWNER, TOKEN


class JobTests(EngineCase):
    async def test_real_shell_stdout_stderr_and_exit_code(self):
        job = await self.execute(code="printf stdout; printf stderr >&2; exit 7")
        self.assertEqual(job["exit_code"], 7)
        self.assertEqual(job["state"], "failed")
        self.assertIn("stdout", job["output"])
        self.assertIn("stderr", job["output"])

    async def test_shell_confirmation_is_single_use(self):
        prepared = self.engine.prepare(OWNER, {"kind": "shell", "payload": {"code": "printf once", "profile": "root"}})
        request = {k: prepared[k] for k in ("id", "token", "digest")}
        job = self.engine.confirm(OWNER, request)
        with self.assertRaises(Denied):
            self.engine.confirm(OWNER, request)
        await self.engine.jobs[job["id"]]

    async def test_wrong_actor_cannot_confirm_read_or_cancel_a_job(self):
        prepared = self.engine.prepare(OWNER, {"kind": "shell", "payload": {"code": "printf private", "profile": "root"}})
        with self.assertRaises(Denied):
            self.engine.confirm(OTHER, {k: prepared[k] for k in ("id", "token", "digest")})
        job = await self.execute(code="printf private")
        with self.assertRaises(Denied):
            self.engine.job(OTHER, job["id"])

    async def test_expired_and_changed_digest_confirmation_rejected(self):
        prepared = self.engine.prepare(OWNER, {"kind": "shell", "payload": {"code": "printf hi"}})
        req = {k: prepared[k] for k in ("id", "token", "digest")}
        with self.assertRaises(Denied):
            self.engine.confirm(OWNER, {**req, "digest": "0" * 64})
        self.engine.store.run("UPDATE pending SET expires=0")
        with self.assertRaises(Denied):
            self.engine.confirm(OWNER, req)

    async def test_discard_really_revokes_confirmation(self):
        prepared = self.engine.prepare(OWNER, {"kind": "shell", "payload": {"code": "printf nope"}})
        await self.engine.dispatch(OWNER, "discard", {"id": prepared["id"], "token": prepared["token"]})
        with self.assertRaises(Denied):
            self.engine.confirm(OWNER, {k: prepared[k] for k in ("id", "token", "digest")})

    async def test_timeout_kills_process_group_and_records_state(self):
        job = await self.execute(code="sleep 10", timeout=1)
        self.assertEqual(job["state"], "timed_out")
        self.assertEqual(self.engine.runner.active, {})

    async def test_pipe_flood_is_capped_without_deadlock(self):
        result = await self.engine.runner.run([sys.executable, "-c", "import sys; sys.stdout.write('x'*500000)"], timeout=10)
        self.assertEqual(result.code, 0)
        self.assertLess(len(result.output), MAX_OUTPUT + 100)
        self.assertIn("capped", result.output)

    async def test_environment_secrets_are_not_inherited_by_job(self):
        os.environ["SENSITIVE_ASSISTANT_TEST"] = "never-copy-this-secret"
        try:
            job = await self.execute(code="printenv SENSITIVE_ASSISTANT_TEST || true")
            self.assertNotIn("never-copy-this-secret", job["output"])
        finally:
            del os.environ["SENSITIVE_ASSISTANT_TEST"]

    async def test_file_execution_uses_exact_reviewed_copy(self):
        meta = self.engine.files.stage(OWNER, "main.py", b"import sys; print('file-ok',sys.argv[1])")
        job = await self.execute(kind="file", upload=meta["id"], entry="main.py", runtime="python", args=["a;echo injected"])
        self.assertEqual(job["exit_code"], 0)
        self.assertEqual(job["output"].strip(), "file-ok a;echo injected")

    async def test_staged_file_and_shell_code_share_private_workspace(self):
        meta = self.engine.files.stage(OWNER, "data.txt", b"file-with-code")
        job = await self.execute(code="cat data.txt", upload=meta["id"])
        self.assertEqual(job["output"], "file-with-code")

    async def test_live_stdin_and_eof(self):
        review = self.engine.prepare(OWNER, {"kind": "shell", "payload": {"code": "read value; printf 'received:%s' \"$value\"", "profile": "root"}})
        job = self.engine.confirm(OWNER, {k: review[k] for k in ("id", "token", "digest")})
        for _ in range(100):
            if job["id"] in self.engine.runner.active:
                break
            await asyncio.sleep(.01)
        await self.engine.dispatch(OWNER, "stdin", {"id": job["id"], "text": "hello"})
        await self.engine.jobs[job["id"]]
        self.assertEqual(self.engine.job(OWNER, job["id"])["output"], "received:hello")

    async def test_cancellation_recorded_and_no_job_replay(self):
        review = self.engine.prepare(OWNER, {"kind": "shell", "payload": {"code": "sleep 10", "profile": "root"}})
        job = self.engine.confirm(OWNER, {k: review[k] for k in ("id", "token", "digest")})
        await asyncio.sleep(.05)
        await self.engine.dispatch(OWNER, "cancel", {"id": job["id"]})
        await self.engine.jobs[job["id"]]
        self.assertEqual(self.engine.job(OWNER, job["id"])["state"], "cancelled")

    async def test_job_code_not_copied_to_audit_or_notifications(self):
        await self.execute(code="printf test-command-content")
        audit = str(self.engine.store.rows("SELECT * FROM audit"))
        events = str(self.engine.store.rows("SELECT * FROM outbox"))
        self.assertNotIn("printf test-command-content", audit + events)


class IPCTests(EngineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.tls = None
        try:
            test = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            test.close()
        except PermissionError:
            certs = self.folder / "tls"
            fingerprint = generate(certs)
            self.engine.policy.frontends[0]["certificate_sha256"] = fingerprint["client"]
            server_tls = {"cert": str(certs / "server.pem"), "key": str(certs / "server.key"), "ca": str(certs / "ca.pem")}
            self.server = await AgentServer(self.engine, self.folder / "agent.sock", tls=server_tls).start()
            self.tls = {"cert": str(certs / "client.pem"), "key": str(certs / "client.key"), "ca": str(certs / "ca.pem"), "port": self.server.port}
        else:
            self.server = await AgentServer(self.engine, self.folder / "agent.sock").start()
        self.client = Client(self.folder / "agent.sock", TOKEN, OWNER, tls=self.tls)

    async def asyncTearDown(self):
        await self.server.close()
        await super().asyncTearDown()

    async def test_real_unix_socket_owner_roundtrip(self):
        status = await self.client.call("status")
        self.assertEqual(status["version"], "1.0.0")
        self.assertFalse(status["settings"]["enabled"])

    async def test_real_socket_rejects_wrong_credential(self):
        with self.assertRaises(Denied):
            await Client(self.folder / "agent.sock", "wrong-token", OWNER, tls=self.tls).call("status")

    async def test_real_socket_rejects_foreign_actor(self):
        with self.assertRaises(Denied):
            await Client(self.folder / "agent.sock", TOKEN, "discord:999999999", tls=self.tls).call("status")

    async def test_real_socket_upload_prepare_execute_and_output(self):
        meta = await self.client.call("upload", name="file.py", data=base64.b64encode(b"print('ipc-file-ok')").decode())
        review = await self.client.call("prepare", kind="file", payload={"upload": meta["id"], "entry": "file.py", "runtime": "python", "profile": "root"})
        job = await self.client.call("confirm", **{k: review[k] for k in ("id", "token", "digest")})
        for _ in range(100):
            result = await self.client.call("job", id=job["id"])
            if result["state"] not in ("running", "queued"):
                break
            await asyncio.sleep(.01)
        self.assertEqual(result["output"].strip(), "ipc-file-ok")

    async def test_unknown_api_command_never_executes(self):
        with self.assertRaises(Denied):
            await self.client.call("automatic-root-shell", code="id")

    async def test_transport_is_local_and_authenticated(self):
        if self.tls:
            self.assertEqual(self.server.server.sockets[0].getsockname()[0], "127.0.0.1")
            self.assertEqual(self.server.server.sockets[0].family.name, "AF_INET")
        else:
            self.assertEqual((self.folder / "agent.sock").stat().st_mode & 0o777, 0o660)
            self.assertEqual(self.server.server.sockets[0].family.name, "AF_UNIX")

    async def test_certificate_pin_rejects_wrong_frontend_even_with_valid_ca(self):
        if self.tls:
            self.engine.policy.frontends[0]["certificate_sha256"] = "0" * 64
            with self.assertRaises(Denied):
                await self.client.call("status")
        else:
            self.engine.policy.frontends[0]["uid"] += 1
            with self.assertRaises(Denied):
                await self.client.call("status")
