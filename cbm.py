"""Run the Polars PDS-H benchmark directly from a CloudBucketMount.

Clone https://github.com/pola-rs/polars-benchmark next to this file, set
``S3_BUCKET``, and run ``modal run cbm.py``. The upstream queries read directly
from the bucket mount, so mountpoint-s3's disk cache is the only input cache
and is limited to the container lifetime. A selector such as ``--queries 1,6``
runs only those upstream query modules.

Set ``RESULTS_VOLUME_NAME`` to override the default results Volume name,
``pdsh-cbm-results``. Set ``S3_ROLE_ARN`` to use OIDC authentication. Without
it, the mount uses the documented ``aws-secret`` Modal Secret for static AWS
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

RESULTS_DIR = Path("/results")
RESULTS_VOLUME_NAME = os.environ.get("RESULTS_VOLUME_NAME", "pdsh-cbm-results")

app = modal.App("polars-pdsh-cbm")
results_volume = modal.Volume.from_name(RESULTS_VOLUME_NAME, create_if_missing=True)


@app.function(
    image=pdsh.image(),
    env={**pdsh.FUNCTION_ENV, "RESULTS_VOLUME_NAME": RESULTS_VOLUME_NAME},
    timeout=24 * 3600,
    volumes={
        RESULTS_DIR: results_volume,
        pdsh.BUCKET_DIR: pdsh.bucket_mount(read_only=True),
    },
)
def read(scale: float, queries: str = "", label: str = "") -> dict[str, Any]:
    """Run selected PDS-H queries directly against the CloudBucketMount."""
    run_dir = f"{label}-{time.time_ns()}" if label else str(time.time_ns())
    timings_dir = RESULTS_DIR / f"scale-{scale}" / run_dir
    return {
        **pdsh.run_queries(
            timings_dir,
            scale,
            queries,
            {"PATH_TABLES": str(pdsh.BUCKET_DIR / pdsh.S3_PREFIX)},
        ),
        "region": os.environ.get("MODAL_REGION"),
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
