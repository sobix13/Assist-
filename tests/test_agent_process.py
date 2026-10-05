import asyncio
import json
import os
import socket
import sys
from pathlib import Path

from maestro.ipc import Client
from maestro.security import Denied, isolated_env
from scripts.create_tls import generate
from .helpers import EngineCase, OWNER, TOKEN, policy


class AgentProcessTests(EngineCase):
    async def test_real_separate_agent_process_owner_job_and_shutdown(self):
        certs = self.folder / "certs"
        hashes = generate(certs)
        config = policy().data
        config["frontends"][0]["certificate_sha256"] = hashes["client"]
        config["coordination_path"] = str(self.folder / "unused-registry.sqlite3")
        policy_path = self.folder / "policy.json"
        policy_path.write_text(json.dumps(config))
        policy_path.chmod(0o600)
        port_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
        port_socket.close()
        args = [sys.executable, "-m", "maestro.agent", "--policy", str(policy_path), "--state", str(self.folder / "child-state"),
                "--socket", str(self.folder / "child.sock"), "--socket-group", "root", "--tls-port", str(port),
                "--tls-cert", str(certs / "server.pem"), "--tls-key", str(certs / "server.key"), "--tls-ca", str(certs / "ca.pem")]
        child = await asyncio.create_subprocess_exec(*args, cwd=Path(__file__).resolve().parents[1], env=isolated_env(self.folder), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        if os.geteuid() != 0:
            output, _ = await child.communicate()
            self.assertIn(b"Run the VPS agent as root", output)
            return
        tls = {"cert": str(certs / "client.pem"), "key": str(certs / "client.key"), "ca": str(certs / "ca.pem"), "port": port}
        client = Client("unused", TOKEN, OWNER, tls=tls)
        try:
            status = None
            for _ in range(100):
                try:
                    status = await client.call("status")
                    break
                except (OSError, ConnectionError):
                    if child.returncode is not None:
                        break
                    await asyncio.sleep(.05)
            self.assertIsNotNone(status)
            self.assertEqual(status["version"], "1.0.0")
            review = await client.call("prepare", kind="shell", payload={"code": "printf separate-process-agent-ok", "profile": "root"})
            job = await client.call("confirm", **{k: review[k] for k in ("id", "token", "digest")})
            for _ in range(100):
                result = await client.call("job", id=job["id"])
                if result["state"] not in ("running", "queued"):
                    break
                await asyncio.sleep(.01)
            self.assertEqual(result["output"], "separate-process-agent-ok")
            with self.assertRaises(Denied):
                await Client("unused", "bad-credential", OWNER, tls=tls).call("status")
        finally:
            if child.returncode is None:
                child.terminate()
            output, _ = await asyncio.wait_for(child.communicate(), 10)
            self.assertNotIn(TOKEN.encode(), output)
            self.assertEqual(child.returncode, 0)
