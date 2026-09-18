# Copyright 2026 Abderahmane Benali
# Licensed under the Apache License, Version 2.0

import asyncio
from collections import deque
import os
import time

from colcon_core.executor import Job
from colcon_core.executor import OnError
from colcon_core.logging import colcon_logger
from colcon_core.subprocess import SIGINT_RESULT
from colcon_parallel_executor.executor.parallel \
    import ParallelExecutorExtension

logger = colcon_logger.getChild(__name__)

GIB = 1024 ** 3


def _meminfo(key):
    with open('/proc/meminfo') as h:
        for line in h:
            if line.startswith(key + ':'):
                return int(line.split()[1]) * 1024
    raise RuntimeError(f"'/proc/meminfo' provides no '{key}'")


def memory_available():
    """
    Get the memory which can be allocated without swapping.

    :returns: The number of bytes
    :rtype: int
    """
    return _meminfo('MemAvailable')


class MemoryGate:
    """Delay jobs while the available memory doesn't cover them."""

    def __init__(self, args):  # noqa: D107
        self.per_job = args.memory_per_job * GIB
        self.settle = args.memory_settle
        self.reserve = args.memory_reserve * GIB
        if args.memory_max:
            # a ceiling for the build is a floor for everything else
            self.reserve = max(
                self.reserve, _meminfo('MemTotal') - args.memory_max * GIB)
        self.started = deque()
        self.running = 0

    def _has_headroom(self):
        now = time.monotonic()
        while self.started and now - self.started[0] > self.settle:
            self.started.popleft()
        # a recently started job hasn't grown into its footprint yet
        # and is therefore not reflected in the available memory
        pending = len(self.started) * self.per_job
        return memory_available() - self.reserve - pending >= self.per_job

    async def acquire(self):
        """Wait until the available memory covers another job."""
        # while no job is running the next one is always admitted, otherwise
        # a reserve larger than the machine would never make progress
        while self.running and not self._has_headroom():
            await asyncio.sleep(1)
        # no await between the check and the bookkeeping, otherwise every
        # ready job passes the same check before any of them is accounted for
        self.started.append(time.monotonic())
        self.running += 1

    def release(self):
        """Account for a job which is no longer running."""
        self.running -= 1


class GatedJob(Job):
    """A job which waits for memory before being performed."""

    gate = None

    async def __call__(self, *args, **kwargs):  # noqa: D102
        try:
            await GatedJob.gate.acquire()
        except asyncio.CancelledError:
            # Job maps a cancellation to the same result, and the executor
            # collects results with .result() which would re-raise instead
            return SIGINT_RESULT
        try:
            return await super().__call__(*args, **kwargs)
        finally:
            GatedJob.gate.release()


class MemoryExecutorExtension(ParallelExecutorExtension):
    """Process multiple packages in parallel within a memory budget."""

    # the priority needs to be higher than the extension providing the
    # parallel execution in order to become the default
    PRIORITY = 120

    def add_arguments(self, *, parser):  # noqa: D102
        # not calling super() since the parallel extension adds its arguments
        # to the same group and add_executor_arguments() would silently skip
        # this extension on the resulting conflict
        parser.add_argument(
            '--memory-reserve', type=float, default=0.0, metavar='NUMBER',
            help='The gibibytes to keep available for other processes, '
                 "or '0' to not limit by memory (default: 0)")
        parser.add_argument(
            '--memory-max', type=float, default=0.0, metavar='NUMBER',
            help='The gibibytes the build may consume, '
                 "or '0' for no limit (default: 0)")
        parser.add_argument(
            '--memory-per-job', type=float, default=2.0, metavar='NUMBER',
            help='The gibibytes to assume a package needs (default: 2.0)')
        parser.add_argument(
            '--memory-settle', type=float, default=20.0, metavar='SECONDS',
            help='The time a started package is assumed to not be reflected '
                 'in the available memory yet (default: 20.0)')
        parser.add_argument(
            '--memory-inner-jobs', type=int, default=0, metavar='NUMBER',
            help='The maximum number of jobs make, Ninja and cargo may use '
                 "within each package, or '0' for no limit (default: 0)")

    def execute(self, args, jobs, *, on_error=OnError.interrupt):  # noqa: D102
        if args.memory_inner_jobs:
            _limit_inner_jobs(args.memory_inner_jobs)
        if args.memory_reserve or args.memory_max:
            GatedJob.gate = MemoryGate(args)
            for job in jobs.values():
                if type(job) is not Job:
                    logger.warning(
                        f"Skipping '{job.identifier}', expected a Job but got "
                        f"a '{type(job).__name__}'")
                    continue
                job.__class__ = GatedJob
        return super().execute(args, jobs, on_error=on_error)


def _limit_inner_jobs(number):
    value = str(number)
    # MAKEFLAGS keeps colcon-cmake from passing its own '-j', Ninja only
    # honors CMAKE_BUILD_PARALLEL_LEVEL and cargo only CARGO_BUILD_JOBS
    os.environ['MAKEFLAGS'] = f'-j{value}'
    os.environ['CMAKE_BUILD_PARALLEL_LEVEL'] = value
    os.environ['CARGO_BUILD_JOBS'] = value
