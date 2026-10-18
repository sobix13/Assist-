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


def fixture_engine(policy, state_dir):
    root = Path(state_dir).parent
    for key, value in {
        'backup_config': str(root/'backup.json'),
        'applications_config': str(root/'applications.json'),
        'recipes_config': str(root/'recipes.json'),
        'coordination_path': str(root/'coordination.sqlite3'),
        'secret_envs': [], 'inventory_roots': [str(root)],
        'database_roots': [str(root)], 'disk_roots': [str(root)],
    }.items():
        policy.data.setdefault(key, value)
    fresh = not (Path(state_dir)/'maestro.sqlite3').exists()
    engine = Engine(policy, state_dir)
    if fresh:
        # Preset operational paths must never become inputs to installer tests.
        for target in engine.store.targets():
            for key in ('install', 'env_file', 'backup_dir', 'heartbeat_file', 'certificate_file'):
                target[key] = ''
            target['database'] = str(engine.store.path) if target['id'] == 'maestro' else ''
            target['repo'] = ''
            engine.store.save_target(target)
    return engine


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
        self.engine = fixture_engine(policy(), self.folder / "state")

    async def asyncTearDown(self):
        await self.engine.close()
        self.temp.cleanup()

    def use_fake_probes(self):
        fake = FakeRunner()
        self.engine.runner = fake
        self.engine.probes = Probes(fake, self.engine.redactor)
        self.engine.inventory.runner = fake
        return fake

    async def execute(self, kind="shell", **payload):
        payload = {"profile": "root", **payload} if kind in ("shell", "file") else payload
        review = await self.engine.dispatch(OWNER, "prepare", {"kind": kind, "payload": payload})
        job = await self.engine.dispatch(OWNER, "confirm", {k: review[k] for k in ("id", "token", "digest")})
        await self.engine.jobs[job["id"]]
        return self.engine.job(OWNER, job["id"])
