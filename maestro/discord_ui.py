from __future__ import annotations

import base64
import io
import json
import time

import discord
from discord import app_commands

from .files import MAX_UPLOAD
from .runbooks import DIAGNOSTICS
from .security import Denied
from .delivery import binary_parts, job_attachments, text_parts

BRANCHES = {
    "setup": ("Setup & schedules", "Select a private report channel, discover installed services, then activate monitoring."),
    "targets": ("Services & health", "Each target has its own unit, paths, health endpoint and recovery flag. Daily probes use read-only database checks."),
    "inventory": ("Host inventory", "Read-only discovery of services, containers, projects, ports, timers and saved PM2 declarations. Finding a resource never takes ownership."),
    "recovery": ("Recovery & diagnostics", "Automatic recovery needs both global and target opt-in. Two observations, 15-minute cooldown, two attempts/hour and three/day."),
    "terminal": ("Terminal", "Run your reviewed code as the restricted runner or root. Output is private. A command job has a timeout and an explicit cancellation action."),
    "files": ("Files & execution", "Upload with /maestro upload. Inspect its manifest and content. Review an entry script or shell command before execution."),
    "jobs": ("Jobs & history", "Inspect job results, provide stdin or cancel a running job. Interrupted jobs are never automatically replayed."),
    "data": ("Backups & restore", "Review coverage, create a verified filesystem/database checkpoint, or stage an offline restore. Encrypted Maestro state downloads are separate."),
    "apps": ("Application settings", "Versioned local adapters validate backend changes and coordinate Discord resource ownership."),
    "access": ("People & access", "Superadmins grant observer/operator access to exact service IDs with expiry. Delegates never grant access onward."),
    "help": ("Guide", "Setup: private channel > discover > inspect target paths > activate. Recovery is opt-in. Terminal access stays bound to your immutable owner ID."),
}


def owner_allowed(bot, interaction):
    return interaction.user.id == bot.owner_id and interaction.guild_id == bot.guild_id


def validate_private_channel(channel, owner_id):
    if not isinstance(channel, discord.TextChannel) or channel.permissions_for(channel.guild.default_role).view_channel:
        raise Denied("Select a text channel hidden from @everyone.")
    bot_id = channel.guild.me.id
    for target, overwrite in channel.overwrites.items():
        if overwrite.view_channel is not True or target.id in (owner_id, bot_id):
            continue
        if isinstance(target, discord.Role) and target.permissions.administrator:
            continue
        if isinstance(target, discord.Role) and target.is_bot_managed() and target.tags and target.tags.bot_id == bot_id:
            continue
        if isinstance(target, discord.Member) and target.guild_permissions.administrator:
            continue
        raise Denied("A non-owner member or non-administrator role has explicit access. Choose an owner channel.")
    permissions = channel.permissions_for(channel.guild.me)
    if not all((permissions.view_channel, permissions.send_messages, permissions.attach_files, permissions.read_message_history, permissions.embed_links)):
        raise Denied("Maestro needs View Channel, Send Messages, Embed Links, Attach Files and Read Message History there.")


async def authorize(bot, interaction):
    if interaction.guild_id != bot.guild_id or getattr(interaction.user, 'bot', False):
        await respond(interaction, 'Use the configured private Maestro server.')
        return False
    if hasattr(bot.api, 'bind'):
        bot.api.bind('discord:'+str(interaction.user.id))
        try:
            await bot.api.call('whoami')
            return True
        except Denied:
            await respond(interaction, 'Access is absent, expired or revoked.')
            return False
    # Test doubles and owner-only compatibility clients keep the strict owner gate.
    if not owner_allowed(bot, interaction):
        await respond(interaction, 'Only the enrolled superadmin has access.')
        return False
    return True


async def respond(i, text=None, **kwargs):
    kwargs["ephemeral"] = True
    kwargs["allowed_mentions"] = discord.AllowedMentions.none()
    if i.response.is_done():
        return await i.followup.send(text, **kwargs)
    return await i.response.send_message(text, **kwargs)


async def show_text(i, text, filename="assistant-report.txt", view=None):
    if len(text) <= 1800:
        await respond(i, text, view=view)
    else:
        for index, (name, data) in enumerate(text_parts(filename, text)):
            await respond(i, "Private report attached.", file=discord.File(io.BytesIO(data), filename=name), view=view if index == 0 else None)


def event_text(event):
    kind, data = event["kind"], event["payload"]
    if kind in ("scheduled_report", "manual_report"):
        lines = ["Maestro | " + data["kind"].title() + " report", "Report ID: " + str(event["id"])]
        for target in data["targets"]:
            lines.append(target["id"] + ": " + ("Checks passed" if target["ok"] else "Needs review"))
            for issue in target.get("issues", []):
                lines.append("  " + issue["code"] + ": " + issue["detail"])
            repo = target.get("repository", {})
            if repo.get("changed"):
                lines.append("  Repository changed: " + repo["url"] + ". Review and retest before updating.")
            elif repo and not repo.get("ok"):
                lines.append("  Repository review unavailable: " + repo["detail"])
        host = data["host"]
        lines.append(f"Host: disk free {host['disk_free_percent']}%, available memory {host['memory_available_percent']}%")
        lines.extend(host["issues"])
        checkpoint = data.get('daily_checkpoint', {})
        if checkpoint:
            lines.append('Daily checkpoint: ' + (checkpoint.get('state', '') + ' ' + checkpoint.get('id', '') if not checkpoint.get('deferred') else 'deferred during maintenance/owner operation'))
        repository = data.get('backup_repository', {})
        if repository:
            lines.append('Backup repository: ' + ('check passed' if repository.get('ok') and not repository.get('deferred') else repository.get('detail', 'needs review')))
        if data.get('new_services'):
            lines.append('New services discovered: ' + ', '.join(data['new_services']) + '. Explicit enrollment required.')
        if data.get('other_failed_services'):
            lines.append('Other failed services: ' + ', '.join(data['other_failed_services']) + '. Observed only; review and enroll before repair.')
        if data.get('service_inventory_error'):
            lines.append(data['service_inventory_error'])
        if data.get('inventory_summary') and not data['inventory_summary']['complete']:
            lines.append('Host inventory is partial. Review errors and scan roots in the inventory panel.')
        return "\n".join(lines)
    return "Maestro | " + kind.replace("_", " ").title() + "\nReport ID: " + str(event["id"]) + "\n" + json.dumps(data, indent=2)


class OwnerView(discord.ui.View):
    def __init__(self, bot, timeout=300):
        super().__init__(timeout=timeout)
        self.bot = bot

    async def interaction_check(self, i):
        return await authorize(self.bot, i)

    async def on_error(self, i, error, item):
        await respond(i, "Operation failed: " + str(error)[:300])


class OwnerModal(discord.ui.Modal):
    def __init__(self, bot, title):
        super().__init__(title=title, timeout=300)
        self.bot = bot

    async def interaction_check(self, i):
        return await authorize(self.bot, i)

    async def on_error(self, i, error):
        await respond(i, "Operation failed: " + str(error)[:300])


class AccessModal(OwnerModal):
    def __init__(self,bot,revoke=False):
        super().__init__(bot,'Revoke access' if revoke else 'Grant scoped access')
        self.revoke=revoke
        self.actor=discord.ui.TextInput(label='Identity: telegram:ID or discord:ID',max_length=40)
        self.add_item(self.actor)
        if not revoke:
            self.role=discord.ui.TextInput(label='observer or operator',default='observer',max_length=8)
            self.targets=discord.ui.TextInput(label='Assigned target IDs, separated by spaces',max_length=2000)
            self.hours=discord.ui.TextInput(label='Duration in hours (1-8760)',default='24',max_length=4)
            for item in (self.role,self.targets,self.hours):self.add_item(item)

    async def on_submit(self,i):
        await i.response.defer(ephemeral=True)
        if self.revoke:value=await self.bot.api.call('access_revoke',actor=str(self.actor))
        else:value=await self.bot.api.call('access_grant',actor=str(self.actor),role=str(self.role),targets=str(self.targets).split(),seconds=int(str(self.hours))*3600)
        await show_text(i,json.dumps(value,indent=2))


class ApplicationModal(OwnerModal):
    def __init__(self,bot):
        super().__init__(bot,'Review application operation')
        self.adapter=discord.ui.TextInput(label='Locally enrolled adapter ID',max_length=30)
        self.action=discord.ui.TextInput(label='settings, provision or publish',default='settings',max_length=9)
        self.changes=discord.ui.TextInput(label='JSON fields to change (blank for provision)',style=discord.TextStyle.paragraph,required=False,max_length=3500)
        for item in (self.adapter,self.action,self.changes):self.add_item(item)

    async def on_submit(self,i):
        current=await self.bot.api.call('application',id=str(self.adapter))
        await review(self.bot,i,'application',{'adapter':str(self.adapter),'action':str(self.action),'revision':current['revision'],'changes':json.loads(str(self.changes) or '{}')})


class PackageModal(OwnerModal):
    def __init__(self,bot):
        super().__init__(bot,'Create categorized ZIP packages')
        self.target=discord.ui.TextInput(label='Target ID or all',default='all',max_length=31)
        self.add_item(self.target)

    async def on_submit(self,i):
        await review(self.bot,i,'package',{'target':str(self.target)})


class ConfirmView(OwnerView):
    def __init__(self, bot, prepared):
        super().__init__(bot)
        self.prepared = prepared

    @discord.ui.button(label="Execute reviewed request", style=discord.ButtonStyle.danger)
    async def execute(self, i, button):
        await i.response.defer(ephemeral=True)
        result = await self.bot.api.call("confirm", id=self.prepared["id"], token=self.prepared["token"], digest=self.prepared["digest"])
        for item in self.children:
            item.disabled = True
        self.stop()
        await respond(i, "Job queued: " + result["id"], view=JobView(self.bot, result["id"]))

    @discord.ui.button(label="Discard", style=discord.ButtonStyle.secondary)
    async def discard(self, i, button):
        await self.bot.api.call("discard", id=self.prepared["id"], token=self.prepared["token"])
        self.stop()
        await i.response.edit_message(content="Request discarded. Nothing was executed.", view=None, attachments=[])


async def review(bot, i, kind, payload):
    await i.response.defer(ephemeral=True)
    prepared = await bot.api.call("prepare", kind=kind, payload=payload)
    text = "Review expires in five minutes. A verified checkpoint is required before execution.\nSHA-256: " + prepared["digest"] + "\n\n" + prepared["preview"]
    await show_text(i, text, "review.txt", ConfirmView(bot, prepared))


class ShellModal(OwnerModal):
    def __init__(self, bot, profile, upload=""):
        super().__init__(bot, "Run code as " + profile)
        self.profile, self.upload = profile, upload
        self.code = discord.ui.TextInput(label="Shell code", style=discord.TextStyle.paragraph, max_length=4000)
        self.cwd = discord.ui.TextInput(label="Working directory (blank = job workspace)", required=False, max_length=400)
        self.timeout = discord.ui.TextInput(label="Timeout in seconds (1-900)", default="120", max_length=3)
        for item in (self.code, self.cwd, self.timeout):
            self.add_item(item)

    async def on_submit(self, i):
        await review(self.bot, i, "shell", {"code": self.code.value, "profile": self.profile, "cwd": self.cwd.value,
                                            "timeout": int(self.timeout.value), "upload": self.upload})


class JsonModal(OwnerModal):
    def __init__(self, bot, mode, value):
        super().__init__(bot, "Edit " + mode)
        self.mode, self.value = mode, value
        content = value if mode == "target" else {k: v for k, v in value.items() if k != "revision"}
        rendered = json.dumps(content, indent=2)
        if len(rendered) > 4000 and mode == 'target':
            rendered = json.dumps({key:value[key] for key in ('id', 'unit', 'revision') if key in value}, indent=2)
        self.text = discord.ui.TextInput(label="JSON fields (existing values are preserved)", style=discord.TextStyle.paragraph, default=rendered, max_length=4000)
        self.add_item(self.text)

    async def on_submit(self, i):
        data = json.loads(self.text.value)
        await i.response.defer(ephemeral=True)
        if self.mode == "target":
            data = {**self.value, **data}
            if self.value.get('revision') is not None and (data['id'] != self.value['id'] or data['revision'] != self.value['revision']):
                raise Denied('Keep the enrolled target ID and revision. Add a separate target for a different identity.')
            result = await self.bot.api.call("save_target", **data)
        else:
            result = await self.bot.api.call("save_settings", revision=self.value["revision"], changes=data)
        await show_text(i, "Saved.\n" + json.dumps(result, indent=2))


class ServiceModal(OwnerModal):
    def __init__(self, bot):
        super().__init__(bot, "Review a service operation")
        self.unit = discord.ui.TextInput(label="Exact unit name", placeholder="ripcars-gate.service", max_length=128)
        self.action = discord.ui.TextInput(label="Action (start, stop, restart, reset-failed)", default="restart", max_length=20)
        self.add_item(self.unit)
        self.add_item(self.action)

    async def on_submit(self, i):
        await review(self.bot, i, "service", {"unit": self.unit.value, "action": self.action.value})


class RebootModal(OwnerModal):
    def __init__(self, bot):
        super().__init__(bot, "Review host reboot")
        self.phrase = discord.ui.TextInput(label="Type REBOOT and the exact VPS hostname", max_length=200)
        self.add_item(self.phrase)

    async def on_submit(self, i):
        await review(self.bot, i, "reboot", {"phrase": self.phrase.value})


class BackupModal(OwnerModal):
    def __init__(self, bot):
        super().__init__(bot, "Encrypted backup")
        self.password = discord.ui.TextInput(label="Backup password (at least 12 characters)", min_length=12, max_length=128)
        self.add_item(self.password)

    async def on_submit(self, i):
        await i.response.defer(ephemeral=True)
        result = await self.bot.api.call("backup", password=self.password.value)
        for name, data in binary_parts(result['name'], base64.b64decode(result['data'])):
            await respond(i, "Encrypted Maestro state. Join numbered binary parts in order if split. SHA-256: " + result["sha256"], file=discord.File(io.BytesIO(data), filename=name))


class InputModal(OwnerModal):
    def __init__(self, bot, job):
        super().__init__(bot, "Send terminal input")
        self.job = job
        self.text = discord.ui.TextInput(label="Input (a newline is appended)", style=discord.TextStyle.paragraph, max_length=4000)
        self.add_item(self.text)

    async def on_submit(self, i):
        await i.response.defer(ephemeral=True)
        await self.bot.api.call("stdin", id=self.job, text=self.text.value)
        await respond(i, "Input sent.")


class JobView(OwnerView):
    def __init__(self, bot, key):
        super().__init__(bot, timeout=900)
        self.key = key

    @discord.ui.button(label="Refresh output", style=discord.ButtonStyle.primary)
    async def refresh(self, i, button):
        await i.response.defer(ephemeral=True)
        result = await self.bot.api.call("job", id=self.key)
        text = f"Job {self.key}\nState: {result['state']}\nExit code: {result['exit_code']}\n\n" + result["output"]
        await show_text(i, text, "terminal-output.txt", JobView(self.bot, self.key))

    @discord.ui.button(label="Download full output", style=discord.ButtonStyle.primary)
    async def download(self, i, button):
        await i.response.defer(ephemeral=True)
        async for name, data in job_attachments(self.bot.api, self.key):
            await respond(i, 'Retained job output attached.', file=discord.File(io.BytesIO(data), filename=name))

    @discord.ui.button(label="Send input", style=discord.ButtonStyle.secondary)
    async def stdin(self, i, button):
        await i.response.send_modal(InputModal(self.bot, self.key))

    @discord.ui.button(label="Close stdin", style=discord.ButtonStyle.secondary)
    async def eof(self, i, button):
        await i.response.defer(ephemeral=True)
        await self.bot.api.call("eof", id=self.key)
        await respond(i, "Terminal input closed.")

    @discord.ui.button(label="Cancel job", style=discord.ButtonStyle.danger)
    async def cancel(self, i, button):
        await i.response.defer(ephemeral=True)
        result = await self.bot.api.call("cancel", id=self.key)
        await respond(i, "Cancellation requested." if result["cancel_requested"] else "Job is already finished.")


class FileModal(OwnerModal):
    def __init__(self, bot, meta, entry, profile):
        super().__init__(bot, "Run reviewed file")
        self.meta, self.entry, self.profile = meta, entry, profile
        self.runtime = discord.ui.TextInput(label="Runtime: python / bash / node", default="bash" if entry.endswith(".sh") else "node" if entry.endswith(".js") else "python", max_length=8)
        self.args = discord.ui.TextInput(label="Arguments as JSON array", default="[]", max_length=2000)
        self.timeout = discord.ui.TextInput(label="Timeout seconds (1-900)", default="120", max_length=3)
        for item in (self.runtime, self.args, self.timeout):
            self.add_item(item)

    async def on_submit(self, i):
        await review(self.bot, i, "file", {"upload": self.meta["id"], "entry": self.entry, "profile": self.profile,
                                          "runtime": self.runtime.value, "args": json.loads(self.args.value), "timeout": int(self.timeout.value)})


class UploadView(OwnerView):
    def __init__(self, bot, meta):
        super().__init__(bot)
        self.meta = meta
        self.entry = meta["files"][0]["name"]
        self.profile = "runner"
        select = discord.ui.Select(placeholder="Select file (first 25 shown)", options=[discord.SelectOption(label=f["name"][-100:], value=str(n), default=n == 0) for n, f in enumerate(meta["files"][:25])])
        async def choose(i):
            self.entry = meta["files"][int(select.values[0])]["name"]
            for option in select.options:
                option.default = meta["files"][int(option.value)]["name"] == self.entry
            await i.response.edit_message(view=self)
        select.callback = choose
        self.add_item(select)
        profile = discord.ui.Select(placeholder="Execution profile", options=[discord.SelectOption(label=x, value=x, default=x == "runner") for x in ("runner", "root")])
        async def select_profile(i):
            self.profile = profile.values[0]
            for option in profile.options:
                option.default = option.value == self.profile
            await i.response.edit_message(view=self)
        profile.callback = select_profile
        self.add_item(profile)

    @discord.ui.button(label="Inspect content", style=discord.ButtonStyle.secondary, row=2)
    async def preview(self, i, button):
        await i.response.defer(ephemeral=True)
        result = await self.bot.api.call("preview", id=self.meta["id"], name=self.entry)
        await show_text(i, result["output"], "file-preview.txt")

    @discord.ui.button(label="Review file execution", style=discord.ButtonStyle.primary, row=2)
    async def execute(self, i, button):
        await i.response.send_modal(FileModal(self.bot, self.meta, self.entry, self.profile))

    @discord.ui.button(label="Code with these files", style=discord.ButtonStyle.secondary, row=2)
    async def code(self, i, button):
        await i.response.send_modal(ShellModal(self.bot, self.profile, self.meta["id"]))


class Panel(OwnerView):
    def __init__(self, bot, section, settings, targets, page=0):
        super().__init__(bot)
        self.section, self.settings, self.targets = section, settings, targets
        self.channel_id = settings["report_channel"]
        menu = discord.ui.Select(placeholder="Choose a section", row=0, options=[discord.SelectOption(label=label, value=key, default=key == section) for key, (label, _) in BRANCHES.items()])
        async def navigate(i):
            await i.response.defer(ephemeral=True)
            await open_panel(bot, i, menu.values[0])
        menu.callback = navigate
        self.add_item(menu)
        actions = {
            "setup": [("Discover services", self.discover), ("Edit schedule", self.edit_settings), ("Activate / Pause", self.toggle)],
            "targets": [("Add target", self.add_target), ("Run hourly checks", self.check_hourly), ("Run daily checks", self.check_daily)],
            "inventory": [("Scan host", self.scan), ("Discover installed presets", self.discover), ("Add target", self.add_target)],
            "recovery": [("Recovery on / off", self.recovery), ("Service action", self.service), ("Maintenance 1 hour", self.maintenance), ("Review host reboot", self.reboot), ("Repair recipes", self.recipe_list)],
            "terminal": [("Runner terminal", self.runner_terminal), ("Root terminal", self.root_terminal), ("Recent jobs", self.jobs)],
            "files": [("Recent uploads", self.uploads)],
            "jobs": [("Recent jobs", self.jobs)],
            "data": [("Backup coverage", self.coverage), ("Create checkpoint", self.checkpoint), ("List checkpoints", self.checkpoint_list), ("ZIP packages", self.package), ("State backup", self.backup)],
            "apps": [("List applications", self.applications), ("Edit / provision", self.application)],
            "access": [("List people", self.access_list), ("Grant access", self.access_grant), ("Revoke access", self.access_revoke)],
            "help": [("Maestro health", self.health)],
        }
        for label, callback in actions[section]:
            b = discord.ui.Button(label=label, row=2, style=discord.ButtonStyle.secondary)
            b.callback = callback
            self.add_item(b)
        if section == "setup":
            picker = discord.ui.ChannelSelect(channel_types=[discord.ChannelType.text], placeholder="Private report channel", row=1,
                      default_values=[discord.Object(id=self.channel_id)] if self.channel_id else [])
            async def selected(i):
                channel = picker.values[0]
                self.channel_id = channel.id
                picker.default_values = [discord.SelectDefaultValue(id=channel.id, type=discord.SelectDefaultValueType.channel)]
                await i.response.edit_message(view=self)
            picker.callback = selected
            self.add_item(picker)
            button = discord.ui.Button(label="Save report channel", row=3, style=discord.ButtonStyle.primary)
            button.callback = self.save_channel
            self.add_item(button)
            create = discord.ui.Button(label="Create owner channel", row=3, style=discord.ButtonStyle.secondary)
            create.callback = self.create_channel
            self.add_item(create)
        if section == "targets" and targets:
            select = discord.ui.Select(placeholder="Edit target | page " + str(page+1), row=1, options=[discord.SelectOption(label=t["id"] + (" (active)" if t["enabled"] else " (not monitored)"), value=t["id"]) for t in targets[page*25:page*25+25]])
            async def edit(i):
                target = next(t for t in self.targets if t["id"] == select.values[0])
                await i.response.send_modal(JsonModal(bot, "target", target))
            select.callback = edit
            self.add_item(select)
            for label, destination in [('Previous', page-1), ('Next', page+1)]:
                if destination < 0 or destination*25 >= len(targets):
                    continue
                button = discord.ui.Button(label=label, row=3)
                async def turn(i, dest=destination):
                    await i.response.edit_message(view=Panel(bot, section, settings, targets, dest))
                button.callback = turn
                self.add_item(button)
        if section == "recovery":
            select = discord.ui.Select(placeholder="Run a read-only diagnostic", row=1, options=[discord.SelectOption(label=key.replace("_", " "), value=key) for key in DIAGNOSTICS])
            async def diagnose(i):
                await i.response.defer(ephemeral=True)
                result = await bot.api.call("diagnostic", name=select.values[0])
                await show_text(i, result["output"])
            select.callback = diagnose
            self.add_item(select)

    async def save(self, i, changes):
        await i.response.defer(ephemeral=True)
        result = await self.bot.api.call("save_settings", revision=self.settings["revision"], changes=changes)
        self.settings = result
        await show_text(i, "Saved.\n" + json.dumps(result, indent=2))

    async def save_channel(self, i):
        channel = i.guild.get_channel(self.channel_id)
        validate_private_channel(channel, self.bot.owner_id)
        await self.save(i, {"report_channel": channel.id})

    async def create_channel(self, i):
        if not i.guild.me.guild_permissions.manage_channels:
            raise Denied("Grant Manage Channels for this optional setup action, or select an existing owner channel.")
        await i.response.defer(ephemeral=True)
        # Refuse same-name adoption. Another bot/admin's channel remains untouched.
        if any(c.name == "maestro-ops" for c in i.guild.text_channels):
            raise Denied("maestro-ops already exists. Select and review it, or rename that channel yourself.")
        owner = i.guild.get_member(self.bot.owner_id) or await i.guild.fetch_member(self.bot.owner_id)
        channel = await i.guild.create_text_channel("maestro-ops", overwrites={
            i.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            owner: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            i.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
        }, reason="Maestro owner requested a private operations channel")
        result = await self.bot.api.call("save_settings", revision=self.settings["revision"], changes={"report_channel": channel.id})
        self.settings = result
        self.channel_id = channel.id
        await respond(i, "Created and bound maestro-ops. Unknown channels and shared bot resources were preserved.")

    async def discover(self, i):
        await i.response.defer(ephemeral=True)
        result = await self.bot.api.call("discover")
        await show_text(i, json.dumps(result, indent=2))

    async def scan(self, i):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call('inventory'), indent=2), 'host-inventory.json')

    async def coverage(self, i):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call('checkpoint_status'), indent=2), 'backup-coverage.json')

    async def checkpoint(self, i):
        await review(self.bot, i, 'checkpoint', {})

    async def checkpoint_list(self, i):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call('checkpoints'), indent=2), 'checkpoints.json')

    async def recipe_list(self, i):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call('recipes'), indent=2), 'repair-recipes.json')

    async def edit_settings(self, i):
        await i.response.send_modal(JsonModal(self.bot, "schedule", self.settings))

    async def toggle(self, i):
        await self.save(i, {"enabled": not self.settings["enabled"]})

    async def recovery(self, i):
        await self.save(i, {"auto_recovery": not self.settings["auto_recovery"]})

    async def maintenance(self, i):
        await self.save(i, {"maintenance_until": time.time() + 3600})

    async def service(self, i):
        await i.response.send_modal(ServiceModal(self.bot))

    async def reboot(self, i):
        await i.response.send_modal(RebootModal(self.bot))

    async def add_target(self, i):
        await i.response.send_modal(JsonModal(self.bot, "target", {"id": "new-service", "unit": "example.service", "enabled": False, "expected_running": False, "auto_restart": False}))

    async def checks(self, i, kind):
        await i.response.defer(ephemeral=True)
        result = await self.bot.api.call("check", kind=kind)
        await show_text(i, json.dumps(result, indent=2), "health-check.json")

    async def check_hourly(self, i):
        await self.checks(i, "hourly")

    async def check_daily(self, i):
        await self.checks(i, "daily")

    async def runner_terminal(self, i):
        await i.response.send_modal(ShellModal(self.bot, "runner"))

    async def root_terminal(self, i):
        await i.response.send_modal(ShellModal(self.bot, "root"))

    async def jobs(self, i):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call("jobs"), indent=2))

    async def uploads(self, i):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call("uploads"), indent=2))

    async def export(self, i):
        await i.response.defer(ephemeral=True)
        data = await self.bot.api.call("export")
        await respond(i, "Operational report attached.", file=discord.File(io.BytesIO(json.dumps(data, indent=2).encode()), filename="assistant-report.json"))

    async def backup(self, i):
        await i.response.send_modal(BackupModal(self.bot))

    async def health(self, i):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call("status"), indent=2))

    async def access_list(self,i):
        await i.response.defer(ephemeral=True)
        await show_text(i,json.dumps(await self.bot.api.call('access_list'),indent=2))

    async def access_grant(self,i):
        await i.response.send_modal(AccessModal(self.bot))

    async def access_revoke(self,i):
        await i.response.send_modal(AccessModal(self.bot,True))

    async def applications(self,i):
        await i.response.defer(ephemeral=True)
        await show_text(i,json.dumps(await self.bot.api.call('applications'),indent=2))

    async def application(self,i):
        await i.response.send_modal(ApplicationModal(self.bot))

    async def package(self,i):
        await i.response.send_modal(PackageModal(self.bot))


async def open_panel(bot, i, section="setup"):
    access = await bot.api.call('whoami')
    if access.get('role') not in ('superadmin', None):
        targets = await bot.api.call('targets')
        await respond(i, 'Maestro | '+access['role'].title()+'\nChoose an assigned service. Terminal, access management and backups belong to the superadmin.', view=DelegatePanel(bot, targets, access['role']))
        return
    settings = await bot.api.call("settings")
    targets = await bot.api.call("targets")
    label, help_text = BRANCHES[section]
    e = discord.Embed(title="Maestro | " + label, description=help_text, color=0x800020)
    e.add_field(name="Monitoring", value="Active" if settings["enabled"] else "Paused")
    e.add_field(name="Automatic recovery", value="Enabled for opted-in targets" if settings["auto_recovery"] else "Off")
    await respond(i, embed=e, view=Panel(bot, section, settings, targets))


class DelegatePanel(OwnerView):
    def __init__(self, bot, targets, role, page=0):
        super().__init__(bot)
        select = discord.ui.Select(placeholder='Assigned service', options=[discord.SelectOption(label=t['id'], value=t['id']) for t in targets[page*25:page*25+25]]) if targets else None
        self.target = targets[page*25]['id'] if targets[page*25:] else ''
        if select:
            async def chosen(i):
                self.target = select.values[0]
                await i.response.edit_message(view=self)
            select.callback = chosen; self.add_item(select)
        for label, action in [('Check', 'check'), ('Logs', 'logs')] + ([('Restart', 'restart')] if role == 'operator' else []):
            button = discord.ui.Button(label=label)
            async def run(i, task=action):
                if task == 'restart':
                    target = next(t for t in await bot.api.call('targets') if t['id'] == self.target)
                    await review(bot, i, 'container' if target['driver']=='docker' else 'service', {'target': self.target, 'unit': target['unit'], 'action': 'restart'})
                else:
                    await i.response.defer(ephemeral=True)
                    await show_text(i, json.dumps(await bot.api.call(task, target=self.target), indent=2))
            button.callback = run; self.add_item(button)
        for label, dest in [('Previous', page-1), ('Next', page+1)]:
            if dest < 0 or dest*25 >= len(targets):
                continue
            button = discord.ui.Button(label=label)
            async def turn(i, destination=dest):
                await i.response.edit_message(view=DelegatePanel(bot, targets, role, destination))
            button.callback = turn; self.add_item(button)


class Commands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="maestro", description="Private VPS operations, monitoring and terminal")
        self.bot = bot

    async def interaction_check(self, i):
        return await authorize(self.bot, i)

    @app_commands.command(description='Superadmin access management with service scope and expiry')
    async def access(self,i:discord.Interaction,revoke:bool=False):
        await i.response.send_modal(AccessModal(self.bot,revoke))

    @app_commands.command(description='Review a typed application change or Verifier setup')
    async def application(self,i:discord.Interaction):
        await i.response.send_modal(ApplicationModal(self.bot))

    @app_commands.command(description='Create categorized ZIPs from a verified checkpoint')
    async def package(self,i:discord.Interaction):
        await i.response.send_modal(PackageModal(self.bot))

    @app_commands.command(description="Open the private owner control center")
    async def panel(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await open_panel(self.bot, i)

    @app_commands.command(description="Read backend health, incidents and configuration")
    async def health(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call("status"), indent=2))

    @app_commands.command(description="Run one target's live passive probes or the full suite")
    async def check(self, i: discord.Interaction, target: str = "", daily: bool = False):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call("check", kind="daily" if daily else "hourly", target=target or None), indent=2), "health-check.json")

    @app_commands.command(description="Read bounded, redacted recent service logs")
    async def logs(self, i: discord.Interaction, target: str):
        await i.response.defer(ephemeral=True)
        await show_text(i, (await self.bot.api.call("logs", target=target))["output"], "service-log.txt")

    @app_commands.command(description="Open a private shell-code editor and review")
    async def terminal(self, i: discord.Interaction, root: bool = False):
        await i.response.send_modal(ShellModal(self.bot, "root" if root else "runner"))

    @app_commands.command(description="Review a start, stop, restart or reset-failed operation")
    async def service(self, i: discord.Interaction):
        await i.response.send_modal(ServiceModal(self.bot))

    @app_commands.command(description="Stage a file or archive without executing it")
    async def upload(self, i: discord.Interaction, file: discord.Attachment):
        if file.size > MAX_UPLOAD:
            await respond(i, "Upload is limited to 8 MiB.")
            return
        await i.response.defer(ephemeral=True)
        data = await file.read()
        meta = await self.bot.api.call("upload", name=file.filename, data=base64.b64encode(data).decode())
        await show_text(i, json.dumps(meta, indent=2), "upload-manifest.json", UploadView(self.bot, meta))

    @app_commands.command(description="Review any listed upload entry, including files beyond the dropdown")
    async def run_file(self, i: discord.Interaction, upload_id: str, entry: str, runtime: str = "python", root: bool = False):
        await review(self.bot, i, "file", {"upload": upload_id, "entry": entry, "runtime": runtime, "profile": "root" if root else "runner", "args": []})

    @app_commands.command(description="Read an existing job and its private terminal output")
    async def job(self, i: discord.Interaction, job_id: str):
        await i.response.defer(ephemeral=True)
        row = await self.bot.api.call("job", id=job_id)
        await show_text(i, json.dumps(row, indent=2), "terminal-output.txt", JobView(self.bot, job_id))

    @app_commands.command(description="Export a redacted operational report without credentials")
    async def export(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call("export"), indent=2), "assistant-report.json")

    @app_commands.command(description="Open the encrypted backup password form")
    async def backup(self, i: discord.Interaction):
        await i.response.send_modal(BackupModal(self.bot))

    @app_commands.command(description="Discover services, containers, projects, timers and ports without adoption")
    async def inventory(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call('inventory'), indent=2), 'host-inventory.json')

    @app_commands.command(description="Download complete retained job output as text, compressed text or numbered files")
    async def output(self, i: discord.Interaction, job_id: str):
        await i.response.defer(ephemeral=True)
        async for name, data in job_attachments(self.bot.api, job_id):
            await respond(i, 'Retained job output attached.', file=discord.File(io.BytesIO(data), filename=name))

    @app_commands.command(description="Review a verified filesystem and database checkpoint before further changes")
    async def checkpoint(self, i: discord.Interaction):
        await review(self.bot, i, 'checkpoint', {})

    @app_commands.command(description="List recent checkpoint records and verification results")
    async def checkpoints(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call('checkpoints'), indent=2), 'checkpoints.json')

    @app_commands.command(description="Review an offline restore to a new private folder without overwriting production")
    async def restore(self, i: discord.Interaction, checkpoint_id: str):
        await review(self.bot, i, 'restore_stage', {'checkpoint': checkpoint_id})

    @app_commands.command(description="List locally reviewed deterministic repair recipes")
    async def recipes(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await show_text(i, json.dumps(await self.bot.api.call('recipes'), indent=2), 'repair-recipes.json')

    @app_commands.command(description="Review a fixed repair with checkpoint, file rollback and health verification")
    async def repair(self, i: discord.Interaction, recipe_id: str):
        await review(self.bot, i, 'recipe', {'recipe': recipe_id})

    @app_commands.command(description="Review start, stop or restart for an enrolled container")
    async def container(self, i: discord.Interaction, target: str, action: str = 'restart'):
        await review(self.bot, i, 'container', {'target': target, 'action': action})

    @app_commands.command(description="Explain setup, jobs, recovery and future adapters")
    async def guide(self, i: discord.Interaction):
        await show_text(i, "1. Enroll both owner IDs and backup coverage using the VPS setup wizard.\n2. Open /maestro panel and bind a private report channel.\n3. Scan Host inventory, discover installed presets, and add other targets. Inspect exact paths and recovery ownership.\n4. Activate monitoring.\n5. Opt selected targets into restart or a locally enrolled recipe, then enable global recovery.\n\nTerminal and files: stage > inspect > review > confirm > verified checkpoint > execute > result. Use /maestro output for the complete retained log, /maestro job for stdin/cancel, and /maestro repair for a fixed recipe. A failed backup blocks execution. Repairs verify health and can restore enrolled code/configuration files. Arbitrary shell side effects require a specific recovery plan.\n\nDaily probes add database integrity, backup age, certificates, inventory and repository changes. Discord and Telegram have separate durable report queues. Open /start in the private Telegram chat. Both linked owner identities see the same jobs and uploads. Offline restores never replace production databases automatically.", 'maestro-guide.txt')
