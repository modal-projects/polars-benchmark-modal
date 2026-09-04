"""Run the Polars PDS-H benchmark over a mix of cached and fresh data.

The other entry points put every table on one input path. This one splits them,
which is the shape of a query that joins tables you have seen before against a
batch that just landed: the static tables are cached in a Modal Volume and the
fact table is fresh on every run.

Two modes decide what happens to the fresh table.

``--mode pull`` copies it into the Volume once per run and the queries read
everything locally, so the run pays one transfer of the fresh table however many
times the queries scan it.

``--mode direct`` leaves it on the bucket and every scan reads it from the mount,
so nothing is transferred twice within a query but every query re-reads it.

The cache Volume defaults to ``pdsh-mixed-data``, separate from ``volume.py``'s,
because a mixed run deletes the cached copy of the fresh table.

Clone https://github.com/pola-rs/polars-benchmark next to this file, set
``S3_BUCKET``, and run ``modal run mixed.py``. ``FRESH_TABLE`` selects the fresh
table and defaults to ``lineitem``, the PDS-H fact table; every other table is
cached. The remaining settings match ``volume.py``.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

import modal

import pdsh

DATA_DIR = Path("/data")
CACHE_DIR = DATA_DIR / "cache"
LAYOUT_DIR = Path("/tmp/mixed")
CACHE_VOLUME_NAME = os.environ.get("CACHE_VOLUME_NAME", "pdsh-mixed-data")
FRESH_TABLE = os.environ.get("FRESH_TABLE", "lineitem")
STATIC_TABLES = tuple(table for table in pdsh.TABLES if table != FRESH_TABLE)

app = modal.App("polars-pdsh-mixed")
cache_volume = modal.Volume.from_name(CACHE_VOLUME_NAME, create_if_missing=True)


@app.function(
    image=pdsh.image(),
    env={
        **pdsh.FUNCTION_ENV,
        "CACHE_VOLUME_NAME": CACHE_VOLUME_NAME,
        "FRESH_TABLE": FRESH_TABLE,
    },
    timeout=12 * 3600,
    volumes={
        DATA_DIR: cache_volume,
        pdsh.BUCKET_DIR: pdsh.bucket_mount(read_only=True),
    },
    cloud=pdsh.PIN_CLOUD,
    region=pdsh.PIN_REGION,
)
def read(
    scale: float, mode: str = "pull", queries: str = "", label: str = ""
) -> dict[str, Any]:
    """Cache the static tables, then run the queries against a fresh fact table."""
    if mode not in ("pull", "direct"):
        raise ValueError(f"unknown mode: {mode!r}")

    source = pdsh.BUCKET_DIR / pdsh.S3_PREFIX / f"scale-{scale}"
    cache = CACHE_DIR / f"scale-{scale}"
    static_sync = pdsh.sync_tables(source, cache, STATIC_TABLES)

    # The bucket's fact table stands in for a batch that has not been read
    # before: dropping the cached copy makes the run pay the transfer a first
    # read would, without needing write access to the bucket.
    fresh_name = f"{FRESH_TABLE}.parquet"
    (cache / fresh_name).unlink(missing_ok=True)
    fresh_pull = (
        pdsh.sync_tables(source, cache, (FRESH_TABLE,)) if mode == "pull" else None
    )
    cache_volume.commit()

    # Upstream resolves every table under one directory, so the two input paths
    # meet in a directory of symlinks rather than in the query code.
    layout = LAYOUT_DIR / f"scale-{scale}"
    layout.mkdir(parents=True, exist_ok=True)
    targets = {
        f"{table}.parquet": cache / f"{table}.parquet" for table in STATIC_TABLES
    }
    targets[fresh_name] = cache / fresh_name if mode == "pull" else source / fresh_name
    for name, target in targets.items():
        (layout / name).unlink(missing_ok=True)
        (layout / name).symlink_to(target)

    run_dir = f"{label}-{time.time_ns()}" if label else str(time.time_ns())
    timings_dir = DATA_DIR / "results" / f"scale-{scale}" / run_dir
    return {
        **pdsh.run_queries(
            timings_dir, scale, queries, {"PATH_TABLES": str(LAYOUT_DIR)}
        ),
        "mode": mode,
        "fresh_table": FRESH_TABLE,
        "static_sync": static_sync,
        "fresh_pull": fresh_pull,
        "region": os.environ.get("MODAL_REGION"),
    }


@app.local_entrypoint()
def main(scale: float = 100.0, mode: str = "pull", queries: str = "") -> None:
    for cpu, memory_mib in pdsh.CONFIGS:
        result = read.with_options(cpu=cpu, memory=memory_mib).remote(
            scale, mode, queries, f"{mode}-cpu-{cpu}-mem-{memory_mib}"
        )
        print(
            json.dumps(
                {"cpu": cpu, "memory_gib": memory_mib / 1024, **result},
                indent=2,
                sort_keys=True,
            )
        )
