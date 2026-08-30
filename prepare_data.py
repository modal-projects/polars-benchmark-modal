"""Generate PDS-H Parquet data and upload it to a user's S3 bucket.

Clone https://github.com/pola-rs/polars-benchmark next to this file, set
``S3_BUCKET``, and run ``modal run prepare_data.py`` once. The generated tables
are kept in a dedicated Modal Volume and uploaded below ``S3_PREFIX``, which
defaults to ``pdsh``, in two layouts: one flat directory per scale factor for
``volume.py`` and ``cbm.py``, and the per-table directories upstream's
``network`` io_type expects for ``s3.py``.

Set ``SEED_VOLUME_NAME`` to override the default seed Volume name, ``pdsh-seed``.
Set ``S3_ROLE_ARN`` to use OIDC authentication. Without it, the mount uses the
documented ``aws-secret`` Modal Secret for static AWS credentials.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import modal

import pdsh

DATA_DIR = Path("/data")
SEED_DIR = DATA_DIR / "tables"
SEED_VOLUME_NAME = os.environ.get("SEED_VOLUME_NAME", "pdsh-seed")

app = modal.App("polars-pdsh-prepare-data")
seed_volume = modal.Volume.from_name(SEED_VOLUME_NAME, create_if_missing=True)


@app.function(
    image=pdsh.image("tpchgen-cli"),
    env={**pdsh.FUNCTION_ENV, "SEED_VOLUME_NAME": SEED_VOLUME_NAME},
    cpu=16,
    memory=64 * 1024,
    timeout=4 * 3600,
    volumes={
        DATA_DIR: seed_volume,
        pdsh.BUCKET_DIR: pdsh.bucket_mount(read_only=False),
    },
)
def seed_bucket(scale: float = 100.0) -> dict[str, Any]:
    """Generate the tables if needed and upload them below S3_PREFIX.

    Both layouts hold the same tables, so the upload moves the dataset twice.
    """
    seed = SEED_DIR / f"scale-{scale}"
    if not all((seed / f"{table}.parquet").exists() for table in pdsh.TABLES):
        seed.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "PYTHONPATH": str(pdsh.REPO_DIR)}
        subprocess.run(
            ["tpchgen-cli", f"--output-dir={seed}", "--format=tbl", "-s", str(scale)],
            cwd=pdsh.REPO_DIR,
            env=env,
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.prepare_data",
                f"--tpch_gen_folder={seed}",
            ],
            cwd=pdsh.REPO_DIR,
            env=env,
            check=True,
        )
        for table in seed.glob("*.tbl"):
            table.unlink()
        seed_volume.commit()

    prefix = pdsh.BUCKET_DIR / pdsh.S3_PREFIX
    started = time.perf_counter()
    uploaded = pdsh.copy_tables(seed, prefix / f"scale-{scale}")

    network_dir = prefix / f"scale-factor-{scale}" / str(pdsh.NETWORK_BATCHES)
    for table in pdsh.TABLES:
        (network_dir / table).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            seed / f"{table}.parquet", network_dir / table / f"{table}.parquet"
        )

    return {
        "uploaded_bytes": 2 * uploaded,
        "seconds": time.perf_counter() - started,
        "uri": f"s3://{pdsh.S3_BUCKET}/{pdsh.S3_PREFIX}/scale-{scale}",
        "network_uri": f"s3://{pdsh.S3_BUCKET}/{network_dir.relative_to(pdsh.BUCKET_DIR)}",
    }


@app.local_entrypoint()
def main(scale: float = 100.0) -> None:
    print(json.dumps(seed_bucket.remote(scale), indent=2, sort_keys=True))
