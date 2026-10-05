from __future__ import annotations

import argparse
import asyncio
import fcntl
import grp
import os
import signal
import time
from pathlib import Path

from .engine import Engine
from .ipc import AgentServer
from .security import Policy
from .watchdog import notify


async def serve(args):
    if os.geteuid() != 0:
        raise SystemExit("Run the VPS agent as root through its service unit.")
    root = Path(args.state)
    root.mkdir(parents=True, exist_ok=True, mode=0o711)
    with (root / "agent.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        policy = Policy.load(args.policy)
        engine = Engine(policy, root)
        tls = {"cert": args.tls_cert, "key": args.tls_key, "ca": args.tls_ca, "port": args.tls_port} if args.tls_port else None
        api = await AgentServer(engine, args.socket, grp.getgrnam(args.socket_group).gr_gid, tls=tls).start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        scheduler = asyncio.create_task(engine.scheduler(), name="maestro-scheduler")
        notify("READY=1\nSTATUS=Owner-bound local broker ready")

        async def heartbeat():
            while not stop.is_set():
                if not scheduler.done() and time.monotonic() - max(engine.scheduler_tick, engine.checkpoints.last_tick) < 240:
                    notify("WATCHDOG=1")
                await asyncio.sleep(20)

        beat = asyncio.create_task(heartbeat())
        try:
            await stop.wait()
        finally:
            notify("STOPPING=1")
            scheduler.cancel()
            beat.cancel()
            await asyncio.gather(scheduler, beat, return_exceptions=True)
            await api.close()
            await engine.close()


def main():
    parser = argparse.ArgumentParser(description="Local, owner-bound Maestro VPS broker")
    parser.add_argument("--policy", default="/etc/maestro/policy.json")
    parser.add_argument("--state", default="/var/lib/maestro-agent")
    parser.add_argument("--socket", default="/run/maestro/agent.sock")
    parser.add_argument("--socket-group", default="maestro")
    parser.add_argument("--tls-port", type=int, default=0, help="Optional mutual-TLS loopback port; Unix socket is the default")
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    parser.add_argument("--tls-ca")
    asyncio.run(serve(parser.parse_args()))


if __name__ == "__main__":
    main()
