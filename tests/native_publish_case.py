"""Run in a child interpreter against the pinned upstream package, without a gateway."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys


async def main():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    source=Path(sys.argv[1]);sys.path.insert(0,str(source))
    spec=importlib.util.spec_from_file_location('native_fakes',Path(__file__).parent/'fixtures/raffle_fakes.py')
    fakes=importlib.util.module_from_spec(spec);spec.loader.exec_module(fakes)
    case=fakes.AsyncCase()
    await case.asyncSetUp()
    try:
        # A broker-created draft must be readable and publishable by Waffle itself.
        from maestro.applications import connect
        from maestro import suite
        cfg,revision=await case.bot.db.settings(1)
        native_spec={'kind':'raffle-v1','guild':1}
        c=connect(case.bot.db.path,False)
        suite.commit(native_spec,c,cfg,revision,'telegram:313342234');c.close()
        rid=await case.draft()
        record=await case.bot.raffles.publish(case.guild,rid,0)
        assert record['status']=='open' and record['message']>0
        assert len(case.guild.public.sent)==1
        try:
            await case.bot.raffles.publish(case.guild,rid,0)
        except ValueError:
            pass
        else:
            raise AssertionError('Duplicate publication was accepted')
        assert len(case.guild.public.sent)==1
        # Once Discord delivery becomes ambiguous, the draft is not blindly replayed.
        next_id=await case.draft()
        case.guild.public.error=RuntimeError('Simulated connection loss')
        try:
            await case.bot.raffles.publish(case.guild,next_id,0)
        except RuntimeError:
            pass
        after=await case.bot.db.get(1,next_id)
        assert after['status']=='publishing'
        try:
            await case.bot.raffles.publish(case.guild,next_id,0)
        except ValueError:
            pass
        else:
            raise AssertionError('Ambiguous publication was replayed')
        print(json.dumps({'native_publication':'passed','no_duplicate':'passed','ambiguous_delivery':'blocked'}))
    finally:
        await case.asyncTearDown()


if __name__=='__main__':asyncio.run(main())
