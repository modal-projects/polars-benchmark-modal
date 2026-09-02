"""Shared plumbing for the PDS-H benchmarks.

The ``volume.py``, ``cbm.py`` and ``s3.py`` modules are the benchmark entry
points, one per input path; this module contains their common configuration,
Modal image, bucket access, and upstream query runner.
"""

import csv
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import modal

BENCHMARK_REPO = os.environ.get("POLARS_BENCHMARK_REPO", "./polars-benchmark")
REPO_DIR = Path("/root/bench")
BUCKET_DIR = Path("/s3")

S3_BUCKET = os.environ.get("S3_BUCKET")
if not S3_BUCKET:
    raise RuntimeError("S3_BUCKET environment variable is required")
S3_PREFIX = os.environ.get("S3_PREFIX", "pdsh")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_ROLE_ARN = os.environ.get("S3_ROLE_ARN")
PIN_CLOUD = os.environ.get("PIN_CLOUD") or None
PIN_REGION = os.environ.get("PIN_REGION") or None

TABLES = (
    "customer",
    "lineitem",
    "nation",
    "orders",
    "part",
    "partsupp",
    "region",
    "supplier",
)
QUERY_COUNT = 22
MANIFEST_NAME = "source-manifest.json"
CHUNK_SIZE = 8 << 20
READ_CONCURRENCY = 8
CONFIGS = ((4, 16 * 1024), (8, 32 * 1024), (32, 128 * 1024))

# Upstream's "network" io_type reads
# <base>/scale-factor-<scale>/<batches>/<table>/*.parquet, so the uploaded
# layout and the benchmark have to agree on the batch count.
NETWORK_BATCHES = 1

POLARS_ENV = {
    "RUN_INCLUDE_IO": "1",
    "RUN_IO_TYPE": "parquet",
    "RUN_POLARS_STREAMING": "1",
    "RUN_LOG_TIMINGS": "1",
    "POLARS_MAX_CONCURRENT_SCANS": "8",
    "POLARS_CONCURRENCY_BUDGET": "512",
    "POLARS_ROW_GROUP_PREFETCH_SIZE": "64",
}

FUNCTION_ENV = {
    "S3_BUCKET": S3_BUCKET,
    "S3_PREFIX": S3_PREFIX,
    "S3_REGION": S3_REGION,
}
if S3_ROLE_ARN:
    FUNCTION_ENV["S3_ROLE_ARN"] = S3_ROLE_ARN
if PIN_CLOUD:
    FUNCTION_ENV["PIN_CLOUD"] = PIN_CLOUD
if PIN_REGION:
    FUNCTION_ENV["PIN_REGION"] = PIN_REGION

_base_image = (
    modal.Image.debian_slim(python_version="3.12")
    .add_local_file(
        f"{BENCHMARK_REPO}/requirements-polars-only.txt",
        "/tmp/requirements.txt",
        copy=True,
    )
    .run_commands(
        "python -m pip install --no-cache-dir uv",
        "uv pip install --system -r /tmp/requirements.txt",
    )
)


def image(*packages: str) -> modal.Image:
    """The benchmark image, with any extra packages installed.

    The upstream repository is added last, so a build step for an entry point's
    own dependencies has to be inserted before it rather than appended.
    """
    installed = (
        _base_image.run_commands(f"uv pip install --system {' '.join(packages)}")
        if packages
        else _base_image
    )
    return installed.add_local_python_source("pdsh").add_local_dir(
        BENCHMARK_REPO, str(REPO_DIR), ignore=["data", ".venv", ".git"]
    )


def bucket_mount(*, read_only: bool) -> modal.CloudBucketMount:
    if S3_ROLE_ARN:
        return modal.CloudBucketMount(
            S3_BUCKET,
            oidc_auth_role_arn=S3_ROLE_ARN,
            read_only=read_only,
        )
    return modal.CloudBucketMount(
        S3_BUCKET,
        secret=modal.Secret.from_name("aws-secret"),
        read_only=read_only,
    )


def copy_tables(source: Path, destination: Path) -> int:
    """Copy all benchmark tables and return the total source size."""
    destination.mkdir(parents=True, exist_ok=True)
    transferred = 0
    for table in TABLES:
        source_path = source / f"{table}.parquet"
        shutil.copyfile(source_path, destination / f"{table}.parquet")
        transferred += source_path.stat().st_size
    return transferred


def sync_tables(source: Path, destination: Path) -> dict[str, Any]:
    """Mirror the bucket's Parquet files into the cache, copying only changes.

    ``MANIFEST_NAME`` beside the cached files records the size and last-modified
    time of every object copied, so a run compares one listing of the prefix
    against it and re-fetches only what differs. S3 objects are immutable, so a
    changed object is copied whole and the unit of re-fetch is one file: a table
    written as several Parquet files costs only the files that changed, while a
    table written as one file costs the whole table.
    """
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / MANIFEST_NAME
    cached = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    current = {}
    for path in sorted(source.rglob("*.parquet")):
        stat = path.stat()
        current[str(path.relative_to(source))] = [stat.st_size, stat.st_mtime]

    started = time.perf_counter()
    copied = [name for name, key in current.items() if cached.get(name) != key]
    for name in copied:
        (destination / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination / name)
    removed = sorted(cached.keys() - current.keys())
    for name in removed:
        (destination / name).unlink(missing_ok=True)
    seconds = time.perf_counter() - started
    manifest_path.write_text(json.dumps(current, sort_keys=True))

    transferred = sum(current[name][0] for name in copied)
    return {
        "files_copied": copied,
        "files_removed": removed,
        "s3_bytes_read": transferred,
        "seconds": seconds,
        "gb_per_second": transferred / 1e9 / seconds if transferred else None,
        "region": os.environ.get("MODAL_REGION"),
    }


def read_throughput(root: Path) -> dict[str, Any]:
    """Read every Parquet file under ``root`` with parallel streams and report GB/s."""
    segments = []
    for path in sorted(root.rglob("*.parquet")):
        size = path.stat().st_size
        step = -(-size // READ_CONCURRENCY)
        segments += [
            (path, offset, min(step, size - offset)) for offset in range(0, size, step)
        ]

    def read_segment(segment: tuple[Path, int, int]) -> int:
        path, offset, length = segment
        read = 0
        with path.open("rb", buffering=0) as file:
            file.seek(offset)
            while read < length and (
                chunk := file.read(min(CHUNK_SIZE, length - read))
            ):
                read += len(chunk)
        return read

    started = time.perf_counter()
    with ThreadPoolExecutor(READ_CONCURRENCY) as pool:
        total = sum(pool.map(read_segment, segments))
    seconds = time.perf_counter() - started
    return {
        "bytes": total,
        "seconds": seconds,
        "gb_per_second": total / 1e9 / seconds,
        "streams": READ_CONCURRENCY,
    }


def run_queries(
    timings_dir: Path, scale: float, queries: str, env: dict[str, str]
) -> dict[str, Any]:
    """Run the selected upstream query modules and parse their timings.

    ``env`` carries the input-path settings of the calling benchmark, such as
    ``PATH_TABLES`` for a local directory or ``RUN_IO_TYPE`` and
    ``PATH_NETWORK_BASE_URL`` for a bucket URI.
    """
    if queries:
        query_numbers = [
            int(query.strip()) for query in queries.split(",") if query.strip()
        ]
        commands = [
            [sys.executable, "-m", f"queries.polars.q{query}"]
            for query in query_numbers
        ]
    else:
        query_numbers = list(range(1, QUERY_COUNT + 1))
        commands = [[sys.executable, "-m", "queries.polars"]]

    timings_dir.mkdir(parents=True, exist_ok=True)
    query_env = {
        **os.environ,
        **POLARS_ENV,
        "PYTHONPATH": str(REPO_DIR),
        "SCALE_FACTOR": str(scale),
        "PATH_TIMINGS": str(timings_dir),
        "PATH_TIMINGS_FILENAME": "timings.csv",
        **env,
    }

    started = time.perf_counter()
    outputs = []
    exit_codes = []
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=REPO_DIR,
            check=False,
            env=query_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        outputs.append(completed.stdout)
        exit_codes.append(completed.returncode)
    wall_seconds = time.perf_counter() - started
    (timings_dir / "runner.log").write_text("".join(outputs))

    timings: dict[int, float] = {}
    timings_csv = timings_dir / "timings.csv"
    if timings_csv.exists():
        with timings_csv.open(newline="") as file:
            timings = {
                int(row["query_number"]): float(row["duration[s]"])
                for row in csv.DictReader(file)
            }

    return {
        "wall_seconds": wall_seconds,
        "query_seconds": timings,
        "failed_queries": sorted(set(query_numbers) - timings.keys()),
        "exit_code": max(exit_codes, default=0),
        "log": str(timings_dir / "runner.log"),
    }
