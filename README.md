# colcon-mem

An extension for [colcon](https://colcon.readthedocs.io) which limits the build
parallelism by the available memory.

`--parallel-workers` limits how many packages are processed at once, but not how
much memory they use. A package whose build spawns its own jobs — `cmake --build`
passing `-j$(nproc)`, or a vendor package invoking cargo — can exhaust the machine
on its own, and the OOM killer then picks a victim anywhere on the system.

## Usage

    colcon build --memory-reserve 4

No package is started while less than 4 GiB are available; the build slows down
instead of failing. Jobs which started within `--memory-settle` seconds are
counted against the budget, since their memory usage isn't visible in
`MemAvailable` yet.

If no job is running at all the next one is always started, so a reserve larger
than the machine degrades to one package at a time rather than never finishing.

### Arguments

    --memory-reserve NUMBER     gibibytes to keep available (0 disables)
    --memory-max NUMBER         gibibytes the build may consume (0 disables)
    --memory-per-job NUMBER     gibibytes to assume a package needs
    --memory-settle SECONDS     grace period before a started job is
                                assumed to be visible in MemAvailable
    --memory-inner-jobs NUMBER  job limit for make, Ninja and cargo
                                within each package

All of them can be set in `~/.colcon/defaults.yaml`:

    build:
      memory-reserve: 4

The extension registers with a higher priority than `parallel` and therefore
becomes the default executor. Without `--memory-reserve` or `--memory-max` it
behaves exactly like `parallel`.

### Limiting the build inside a package

`--memory-inner-jobs` sets `MAKEFLAGS`, `CMAKE_BUILD_PARALLEL_LEVEL` and
`CARGO_BUILD_JOBS`. All three are needed: `MAKEFLAGS` keeps colcon-cmake from
appending its own `-j`, but Ninja doesn't read `MAKEFLAGS` and only honors
`CMAKE_BUILD_PARALLEL_LEVEL`, and neither reaches cargo.

## Limiting the whole build

This extension only controls when colcon starts a package. It cannot see the
processes a package spawns, so it cannot prevent a single vendor package from
exhausting the machine. A cgroup can:

    systemd-run --user --scope -p MemoryMax=12G -p MemorySwapMax=0 \
      colcon build

A build which exceeds the limit is then terminated inside its own scope instead
of triggering a system-wide OOM kill. `MemorySwapMax=0` is intentional — a
compiler thrashing on swap is far worse than a failed build.

## Installation

    pip install --user -e .

Requires Linux (`/proc/meminfo`).

## Tests

    python -m pytest
