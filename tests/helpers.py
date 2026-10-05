import os
import pwd
import tempfile
import unittest
from pathlib import Path

from maestro.engine import Engine
from maestro.process import Result
from maestro.probes import Probes
from maestro.security import Policy

OWNER = "discord:123456789"
OTHER = "discord:234567890"
TOKEN = "test-local-frontend-credential-with-more-than-forty-characters"


def policy():
    return Policy({"owners": [OWNER, OTHER], "frontends": [{"uid": os.getuid(), "token": TOKEN, "transport": "discord"}],
                   "runner_user": "nobody" if os.geteuid() == 0 else pwd.getpwuid(os.getuid()).pw_name,
                   "root_commands": True, "host_reboot": False, 'backup_required': False})


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.states = {}
        self.logs = {}
        self.active = {}
        self.buffers = {}

    async def run(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:2] == ["systemctl", "show"]:
            state = self.states.get(argv[2], "active")
            return Result(0, f"LoadState=loaded\nActiveState={state}\nSubState=running\nWatchdogUSec=0\nMainPID=42\nNRestarts=0\n")
        if argv[0] == "journalctl":
            return Result(0, self.logs.get(argv[2], ""))
        if argv[:2] == ["systemctl", "restart"]:
            self.states[argv[2]] = "active"
        return Result(0, "template-ok")


class EngineCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="maestro-test-")
        self.folder = Path(self.temp.name)
        self.folder.chmod(0o711)
        self.engine = Engine(policy(), self.folder / "state")

    async def asyncTearDown(self):
        await self.engine.close()
        self.temp.cleanup()

    def use_fake_probes(self):
        fake = FakeRunner()
        self.engine.runner = fake
        self.engine.probes = Probes(fake, self.engine.redactor)
        return fake

    async def execute(self, kind="shell", **payload):
        payload = {"profile": "root", **payload} if kind in ("shell", "file") else payload
        review = await self.engine.dispatch(OWNER, "prepare", {"kind": kind, "payload": payload})
        job = await self.engine.dispatch(OWNER, "confirm", {k: review[k] for k in ("id", "token", "digest")})
        await self.engine.jobs[job["id"]]
        return self.engine.job(OWNER, job["id"])
