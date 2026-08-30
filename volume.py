"""Run the Polars PDS-H benchmark with a persistent read-through cache.

Clone https://github.com/pola-rs/polars-benchmark next to this file, set
``S3_BUCKET``, and run ``modal run volume.py``. The benchmark fills a persistent
Modal Volume from the CloudBucketMount, copying only the Parquet files whose
bucket object is new or has changed since the last run, then points upstream
queries at the Volume. A selector such as ``--queries 1,6`` runs only those
upstream query modules.

Set ``CACHE_VOLUME_NAME`` to override the default cache Volume name,
``pdsh-data``. Set ``S3_ROLE_ARN`` to use OIDC authentication. Without it, the
mount uses the documented ``aws-secret`` Modal Secret for static AWS
credentials. ``S3_PREFIX`` defaults to ``pdsh`` and
``POLARS_BENCHMARK_REPO`` defaults to ``./polars-benchmark``.
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
CACHE_VOLUME_NAME = os.environ.get("CACHE_VOLUME_NAME", "pdsh-data")

app = modal.App("polars-pdsh-volume")
cache_volume = modal.Volume.from_name(CACHE_VOLUME_NAME, create_if_missing=True)


@app.function(
    image=pdsh.image(),
    env={**pdsh.FUNCTION_ENV, "CACHE_VOLUME_NAME": CACHE_VOLUME_NAME},
    timeout=4 * 3600,
    volumes={
        DATA_DIR: cache_volume,
        pdsh.BUCKET_DIR: pdsh.bucket_mount(read_only=True),
    },
)
def read(scale: float, queries: str = "", label: str = "") -> dict[str, Any]:
    """Refresh the Volume cache from the bucket, then run selected PDS-H queries."""
    s3_ingest = pdsh.sync_tables(
        pdsh.BUCKET_DIR / pdsh.S3_PREFIX / f"scale-{scale}",
        CACHE_DIR / f"scale-{scale}",
    )
    if s3_ingest["files_copied"] or s3_ingest["files_removed"]:
        cache_volume.commit()

    run_dir = f"{label}-{time.time_ns()}" if label else str(time.time_ns())
    timings_dir = DATA_DIR / "results" / f"scale-{scale}" / run_dir
    return {
        **pdsh.run_queries(
            timings_dir, scale, queries, {"PATH_TABLES": str(CACHE_DIR)}
        ),
        "s3_ingest": s3_ingest,
    }


@app.local_entrypoint()
def main(scale: float = 100.0, queries: str = "") -> None:
    for cpu, memory_mib in pdsh.CONFIGS:
        result = read.with_options(cpu=cpu, memory=memory_mib).remote(
            scale, queries, f"cpu-{cpu}-mem-{memory_mib}"
        )
        print(
            json.dumps(
                {"cpu": cpu, "memory_gib": memory_mib / 1024, **result},
                indent=2,
                sort_keys=True,
            )
        )
