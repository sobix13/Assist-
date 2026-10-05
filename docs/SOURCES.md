# Implementation references

- Python asynchronous subprocesses, bounded draining and timeout handling: https://docs.python.org/3.12/library/asyncio-subprocess.html
- Python archive member and extraction behavior: https://docs.python.org/3.12/library/tarfile.html
- Discord interaction/view response and ephemeral UI: https://discordpy.readthedocs.io/en/stable/interactions/api.html
- GitHub default-branch commit API and rate/error behavior: https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28
- systemd readiness/watchdogs and restart behavior: https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html

These describe the external interfaces. The shipped validation files separately record what was executed locally and what needs live acceptance on the owner's VPS/server.
