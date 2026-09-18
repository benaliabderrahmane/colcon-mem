# Copyright 2026 Abderahmane Benali
# Licensed under the Apache License, Version 2.0

import asyncio

from colcon_mem.executor import mem


class Args:
    """Stand-in for the parsed command line arguments."""

    memory_per_job = 2.0
    memory_reserve = 4.0
    memory_max = 0.0
    memory_settle = 20.0


def _available(gib):
    mem.memory_available = lambda: gib * mem.GIB


def test_only_admits_what_the_memory_covers():
    # 20 jobs against a constant 20 GiB: without accounting for the jobs
    # which have started but not grown yet all of them would be admitted
    async def run():
        _available(20)
        gate = mem.MemoryGate(Args())
        pending = [asyncio.ensure_future(gate.acquire()) for _ in range(20)]
        await asyncio.sleep(0.05)
        assert gate.running == 8
        for future in pending:
            future.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(run())


def test_admits_more_once_started_jobs_are_reflected():
    async def run():
        _available(20)
        args = Args()
        args.memory_settle = 0.01
        gate = mem.MemoryGate(args)
        for _ in range(8):
            await gate.acquire()
        await asyncio.sleep(0.05)
        await asyncio.wait_for(gate.acquire(), 1)
        assert gate.running == 9

    asyncio.run(run())


def test_makes_progress_without_any_memory():
    async def run():
        _available(0)
        gate = mem.MemoryGate(Args())
        await asyncio.wait_for(gate.acquire(), 1)
        assert gate.running == 1
        try:
            await asyncio.wait_for(gate.acquire(), 0.2)
        except asyncio.TimeoutError:
            pass
        else:
            assert False, 'the second job should have been delayed'

    asyncio.run(run())


def test_maximum_implies_a_reserve():
    _available(20)
    args = Args()
    args.memory_reserve = 0.0
    args.memory_max = 1.0
    assert mem.MemoryGate(args).reserve > 8 * mem.GIB


def test_cancelled_while_waiting_reports_sigint():
    # the executor collects results with .result() which re-raises a
    # cancellation, so a waiting job has to return like Job does
    async def run():
        _available(0)
        gate = mem.MemoryGate(Args())
        gate.running = 1
        mem.GatedJob.gate = gate
        job = mem.GatedJob.__new__(mem.GatedJob)
        future = asyncio.ensure_future(job())
        await asyncio.sleep(0.05)
        future.cancel()
        assert await future == 'SIGINT'
        assert not future.cancelled()

    asyncio.run(run())
