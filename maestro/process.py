from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from .security import isolated_env

MAX_OUTPUT = 128 * 1024


@dataclass
class Result:
    code: int
    output: str
    state: str = "completed"
    total_bytes: int = 0
    truncated: bool = False


class Runner:
    """Finite jobs with bounded pipes, process-group cleanup and no inherited secrets."""
    def __init__(self):
        self.active = {}
        self.buffers = {}

    def output(self, key):
        return self.buffers.get(key, bytearray()).decode("utf-8", "replace")

    @staticmethod
    def kill_group(proc, sig):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass

    async def run(self, argv, *, cwd=None, timeout=30, key=None, open_stdin=False, log_path=None, log_limit=32*1024*1024):
        cwd = Path(cwd or "/tmp").resolve()
        log = None
        if log_path:
            fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            log = os.fdopen(fd, 'wb')
        try:
            proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=isolated_env(cwd),
                        stdin=asyncio.subprocess.PIPE if open_stdin else asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        except BaseException:
            if log:
                log.close()
            raise
        key = key or str(proc.pid)
        self.active[key] = proc
        buffer = self.buffers[key] = bytearray()
        truncated = False
        total = 0
        limited = False

        async def drain():
            nonlocal truncated, total, limited
            try:
                while block := await proc.stdout.read(4096):
                    if log:
                        room = max(0, log_limit - total)
                        log.write(block[:room])
                        if len(block) > room:
                            limited = True
                            self.kill_group(proc, signal.SIGKILL)
                    total += len(block)
                    remaining = MAX_OUTPUT - len(buffer)
                    buffer.extend(block[:max(0, remaining)])
                    if len(block) > remaining:
                        truncated = True
            except BaseException:
                self.kill_group(proc, signal.SIGKILL)
                raise

        reader = asyncio.create_task(drain())
        state = "completed"
        try:
            await asyncio.wait_for(proc.wait(), timeout)
        except asyncio.TimeoutError:
            state = "timed_out"
            self.kill_group(proc, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except asyncio.TimeoutError:
                self.kill_group(proc, signal.SIGKILL)
                await proc.wait()
        except asyncio.CancelledError:
            self.kill_group(proc, signal.SIGKILL)
            await proc.wait()
            state = "cancelled"
            raise
        finally:
            # A background child must not survive a finished terminal job or keep pipes open.
            self.kill_group(proc, signal.SIGKILL)
            try:
                await asyncio.wait_for(reader, 2)
            except asyncio.TimeoutError:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            finally:
                self.active.pop(key, None)
                if log:
                    try:
                        log.flush()
                        os.fsync(log.fileno())
                    finally:
                        log.close()
        if limited:
            state = 'output_limited'
        output = self.output(key) + ("\n[Output capped at 128 KiB]" if truncated else "")
        self.buffers.pop(key, None)
        return Result(proc.returncode, output, state, total, limited if log_path else truncated)

    async def send_input(self, key, text):
        proc = self.active.get(key)
        if not proc or not proc.stdin or proc.returncode is not None:
            raise ValueError("This job has no open terminal input.")
        if len(text.encode()) > 4096:
            raise ValueError("Input is limited to 4096 bytes.")
        proc.stdin.write((text + "\n").encode())
        await asyncio.wait_for(proc.stdin.drain(), 3)

    def cancel(self, key):
        proc = self.active.get(key)
        if proc:
            self.kill_group(proc, signal.SIGKILL)
            return True
        return False
