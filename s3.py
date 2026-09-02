"""Run the Polars PDS-H benchmark straight from S3, with no cache layer.

Clone https://github.com/pola-rs/polars-benchmark next to this file, set
``S3_BUCKET``, run ``modal run prepare_data.py`` once, then
``modal run s3.py``. The queries use upstream's ``network`` io_type, so Polars'
own object-store reader fetches every column range over HTTPS on each run. That
traffic crosses the container's network namespace, so this is also the input
path whose S3 bytes can be measured from inside the run: the reported
``s3_bytes_received`` is the container's received-byte counter across the suite.

A selector such as ``--queries 1,6`` runs only those upstream query modules.
Each query runs as its own process with freshly minted credentials, so a suite
longer than the STS session lifetime cannot expire mid-run.

Set ``RESULTS_VOLUME_NAME`` to override the default results Volume name,
``pdsh-s3-results``. Set ``S3_ROLE_ARN`` to use OIDC authentication. Without it,
the queries use the documented ``aws-secret`` Modal Secret for static AWS
credentials. ``S3_PREFIX`` defaults to ``pdsh``, ``S3_REGION`` to ``us-east-1``
and ``POLARS_BENCHMARK_REPO`` to ``./polars-benchmark``.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import modal

import pdsh

RESULTS_DIR = Path("/results")
RESULTS_VOLUME_NAME = os.environ.get("RESULTS_VOLUME_NAME", "pdsh-s3-results")
image = pdsh.image("boto3")

with image.imports():
    import boto3

app = modal.App("polars-pdsh-s3")
results_volume = modal.Volume.from_name(RESULTS_VOLUME_NAME, create_if_missing=True)


def query_env() -> dict[str, str]:
    """Point the upstream queries at the bucket, with usable credentials."""
    env = {
        "RUN_IO_TYPE": "network",
        "PATH_NETWORK_BASE_URL": f"s3://{pdsh.S3_BUCKET}/{pdsh.S3_PREFIX}",
        "NUM_BATCHES": str(pdsh.NETWORK_BATCHES),
        "AWS_REGION": pdsh.S3_REGION,
    }
    if not pdsh.S3_ROLE_ARN:
        return env
    sts = boto3.client("sts", region_name=pdsh.S3_REGION)
    credentials = sts.assume_role_with_web_identity(
        RoleArn=pdsh.S3_ROLE_ARN,
        RoleSessionName="polars-pdsh-benchmark",
        WebIdentityToken=os.environ["MODAL_IDENTITY_TOKEN"],
    )["Credentials"]
    return {
        **env,
        "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
        "AWS_SESSION_TOKEN": credentials["SessionToken"],
    }


def s3_read_throughput(scale: float, credentials: dict[str, str]) -> dict[str, Any]:
    """Stream the flat-layout Parquet objects out of S3 and report GB/s."""
    client_args = {"region_name": pdsh.S3_REGION}
    if "AWS_ACCESS_KEY_ID" in credentials:
        client_args.update(
            aws_access_key_id=credentials["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=credentials["AWS_SECRET_ACCESS_KEY"],
            aws_session_token=credentials["AWS_SESSION_TOKEN"],
        )
    client = boto3.client("s3", **client_args)
    prefix = f"{pdsh.S3_PREFIX}/scale-{scale}/"
    segments = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=pdsh.S3_BUCKET, Prefix=prefix
    ):
        for item in page.get("Contents", []):
            key = item["Key"]
            if not key.endswith(".parquet"):
                continue
            size = item["Size"]
            step = -(-size // pdsh.READ_CONCURRENCY)
            segments += [
                (key, offset, min(step, size - offset))
                for offset in range(0, size, step)
            ]

    def read_segment(segment: tuple[str, int, int]) -> int:
        key, offset, length = segment
        body = client.get_object(
            Bucket=pdsh.S3_BUCKET,
            Key=key,
            Range=f"bytes={offset}-{offset + length - 1}",
        )["Body"]
        read = 0
        while read < length and (
            chunk := body.read(min(pdsh.CHUNK_SIZE, length - read))
        ):
            read += len(chunk)
        body.close()
        return read

    started = time.perf_counter()
    with ThreadPoolExecutor(pdsh.READ_CONCURRENCY) as pool:
        total = sum(pool.map(read_segment, segments))
    seconds = time.perf_counter() - started
    return {
        "bytes": total,
        "seconds": seconds,
        "gb_per_second": total / 1e9 / seconds,
        "streams": pdsh.READ_CONCURRENCY,
    }


def received_bytes() -> int:
    """Bytes received on the container's non-loopback interfaces."""
    lines = Path("/proc/net/dev").read_text().splitlines()[2:]
    counters = (line.split(":") for line in lines)
    return sum(
        int(fields.split()[0])
        for interface, fields in counters
        if interface.strip() != "lo"
    )


@app.function(
    image=image,
    env={**pdsh.FUNCTION_ENV, "RESULTS_VOLUME_NAME": RESULTS_VOLUME_NAME},
    secrets=[] if pdsh.S3_ROLE_ARN else [modal.Secret.from_name("aws-secret")],
    timeout=24 * 3600,
    volumes={RESULTS_DIR: results_volume},
    cloud=pdsh.PIN_CLOUD,
    region=pdsh.PIN_REGION,
)
def read(scale: float, queries: str = "", label: str = "") -> dict[str, Any]:
    """Run selected PDS-H queries against the bucket, one process per query."""
    selectors = queries.split(",") if queries else range(1, pdsh.QUERY_COUNT + 1)
    run_dir = f"{label}-{time.time_ns()}" if label else str(time.time_ns())
    timings_dir = RESULTS_DIR / f"scale-{scale}" / run_dir

    before = received_bytes()
    started = time.perf_counter()
    credentials = query_env()
    read_throughput = s3_read_throughput(scale, credentials)
    runs = [
        pdsh.run_queries(
            timings_dir / f"q{selector}", scale, str(selector), credentials
        )
        for selector in selectors
    ]
    return {
        "wall_seconds": time.perf_counter() - started,
        "s3_bytes_received": received_bytes() - before,
        "query_seconds": {
            query: seconds
            for run in runs
            for query, seconds in run["query_seconds"].items()
        },
        "failed_queries": sorted(
            query for run in runs for query in run["failed_queries"]
        ),
        "exit_code": max(run["exit_code"] for run in runs),
        "log": str(timings_dir),
        "region": os.environ.get("MODAL_REGION"),
        "read_throughput": read_throughput,
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
