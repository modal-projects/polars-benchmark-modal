# polars-benchmark-modal

Run the [Polars PDS-H benchmark](https://github.com/pola-rs/polars-benchmark)
(a TPC-H derivative, 22 queries) on [Modal](https://modal.com), reading Parquet
from your own S3 bucket.

It measures one decision: where the queries read their input from, and where the
container reading it runs.

| file | input path |
|---|---|
| [`volume.py`](volume.py) | a Modal Volume, refreshed from the bucket, then read locally |
| [`cbm.py`](cbm.py) | a `CloudBucketMount` over the bucket, on every read |
| [`s3.py`](s3.py) | `s3://` straight from Polars, on every read |
| [`mixed.py`](mixed.py) | static tables from a Volume, one fresh table from the bucket |

[`pdsh.py`](pdsh.py) holds the shared image, Polars settings and query runner;
[`prepare_data.py`](prepare_data.py) generates the dataset if you lack one.

## Results

Scale factor 100 (26.5 GB of Parquet, eight tables), one container, 4 CPU /
16 GiB, bucket in `us-east-1`. Query seconds come from the upstream runner's
`timings.csv`. Dollars are [Modal](https://modal.com/pricing) and AWS list-price
arithmetic, not an invoice.

### The input path decides speed

![Read speed by input path](docs/read-speed.png)

A sequential pass over the whole dataset with eight parallel streams and no query
engine: 2.42 GB/s off a Volume, 0.57 GB/s through the mount, 0.23 GB/s from
`s3://`. The Volume is the one path whose speed does not depend on where the
container landed.

That carries into the suite. The same 22 queries take 157s off a warm Volume and
1956s from `s3://` in the bucket's own region, a per-query geometric mean of 2.28s
against 31.76s.

![Query time by input path](docs/input-path.png)

The mount is erratic rather than uniformly slow: its raw read speed beats Polars'
S3 reader, but q1 took 123s unpinned and 629s pinned, and the full suite 12951s
unpinned. A pinned full suite was preempted before finishing, so there is no
pinned suite number here.

### Placement decides transfer cost

Modal automatically uses an
[S3 Gateway endpoint](https://modal.com/docs/guide/s3-gateway-endpoints) when a
container runs on AWS, so a container reading a bucket in its own AWS region pays
no transfer charge. Off-region pays inter-region rates and another cloud pays
internet egress. Unpinned containers land wherever there is capacity, so both
cases are ordinary. Placement costs 1.5x compute for a broad selector and 1.75x
for an exact region.

| requested region | landed | warm suite | cold suite, with 26.5 GB ingest | cost, warm / cold |
|---|---|---:|---:|---:|
| none | southcentralus | 157s | 206s | $0.014 / $2.41 |
| `us` | us-east-1 | 291s | 323s, 82.8s at 0.320 GB/s | $0.038 / $0.043 |
| `us-east` | us-east-2 (warm), us-east-1 (cold) | 278s | 276s, 83.0s at 0.319 GB/s | $0.037 / $0.036 |
| `us-east-1` | us-east-1 | 321s | 315s, 84.5s at 0.314 GB/s | $0.049 / $0.049 |

Three things follow. The landed region, not the requested selector, decides
whether a read is free: `us-east` landed in `us-east-2` on the warm run, which is
inter-region against this bucket. In-region ingest ran at the same speed whatever
the selector, so the durable difference between placements is the compute
multiplier, and the $2.41 unpinned cold run is off-region transfer, not slowness.
And pinning is not faster: the warm suite took 157s unpinned against 314s, 321s
and 353s pinned, slower on all 22 queries, because pinned capacity is a narrower
pool. Per-query times track each other within roughly 10% to 25% across all four
placements, so placement moves the bill rather than the performance profile.

### What a run costs

| per 22-query run | time | S3 read | compute | transfer | total |
|---|---:|---:|---:|---:|---:|
| Volume, warm, unpinned | 157s | 0 | $0.014 | $0 | **$0.014** |
| Volume, warm, pinned | 321s | 0 | $0.049 | $0 | **$0.049** |
| Volume, first fill, pinned | 84s + 315s | 26.5 GB | $0.062 | $0 | **$0.062** |
| Volume, first fill, unpinned | 253s + 157s | 26.5 GB | $0.036 | $2.39 | **$2.43** |
| `s3://`, pinned | 1956s | 351.3 GB | $0.301 | $0 | **$0.301** |
| `s3://`, unpinned | 3467s | 344.6 GB | $0.305 | $31.01 | **$31.32** |
| `CloudBucketMount`, unpinned | 12951s | not measurable | $1.14 | modeled | - |

![Cost per 22-query run](docs/cost.png)

So the cheapest arrangement is neither of the obvious two. Fill the cache from a
pinned container, where the one-time copy is free, then run the queries unpinned
off the Volume, where compute is cheapest and nothing transfers. Over ten suite
runs a month that is **$0.15** plus $2.22 of storage, against $3.01 for pinned
`s3://` and $313.20 for unpinned `s3://`.

## Choosing an input path

Cache when the same bytes are read more than once. For `Q` queries over `D` GB,
each query reading a fraction `f` of it, at `e` per GB of transfer and `c` per
second of compute:

- from the bucket every time: `Q * (t_bucket * c + f * D * e)`
- fill a Volume once, then read it: `D * e + t_fill * c + Q * (t_volume * c)`
  plus storage

Pinning sets `e` to zero and raises `c`; reuse pays for the fill and the storage.
Above, the fill costs $0.06 and each suite saves $0.287 against pinned `s3://`,
so the copy pays for itself on the first run and the storage after about eight.

The case against caching is data read once: a job over the last few minutes of
trading data reads a window that was never in the cache and will not be queried
again. Time-partitioning does not help, since the newest partition is always
cold. That case is worth measuring rather than assuming, which is what
[`mixed.py`](mixed.py) does. It caches the static tables and treats `lineitem`,
18 GB of the 26.5 GB, as a batch never read before, then runs q3, which joins two
cached tables against it.

| fresh table, static tables on a Volume | requested region | fresh transfer | q3 |
|---|---|---:|---:|
| staged into the Volume, then queried | `us-east-1` | 41s for 18 GB | 9.5s |
| read from the mount on every scan | `us-east-1` | none up front | 687s |
| staged into the Volume, then queried | none | 82s for 18 GB | 9.6s |
| read from the mount on every scan | none | none up front | 206s |

Staging data read exactly once still won, by more than the copy cost, in region
and out of it. A query does not read an object once: it goes back to it, and each
round trip pays the mount's request latency, while the copy pays it once and the
query then runs at Volume speed. Read the mount rows as hundreds of seconds
rather than exact figures.

## Keeping the cache fresh

Every run lists the prefix and compares each object's size and last-modified time
against a manifest kept beside the cached files, then copies what is new or
changed and deletes what is gone. An unchanged dataset costs one listing and no
transfer.

The unit of re-fetch is one file, since S3 objects are immutable, so a table held
as a single Parquet file is recopied whole even for a one-row edit. Partition the
tables you expect to update, which upstream supports: set `NUM_BATCHES=<n>` and
lay them out as `scale-<scale>/<n>/<table>/<batch>/part.parquet`. The cache
mirrors whatever tree is under the prefix, so a rewritten partition costs one
file, not a table.

## Setup

```bash
# in this repo
git clone https://github.com/pola-rs/polars-benchmark   # the benchmark itself
pip install modal && modal setup            # or: uv pip install modal && modal setup

export S3_BUCKET=your-bucket
export S3_ROLE_ARN=arn:aws:iam::...:role/your-role      # optional, see below
```

With `S3_ROLE_ARN` set, the container exchanges its own
[Modal OIDC identity](https://modal.com/docs/guide/cloud-bucket-mounts) for
short-lived credentials, so no long-lived AWS keys are involved. Without it,
create a Modal Secret named `aws-secret` holding `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY`.

To place containers next to the bucket, set `PIN_CLOUD` and `PIN_REGION`:

```bash
export PIN_CLOUD=aws PIN_REGION=us-east   # or PIN_REGION=us-east-1, see below
```

Modal's public [region options](https://modal.com/docs/guide/region-selection)
stop at `us-east`, which covers `us-east-1` and `us-east-2`, so a bucket in
`us-east-1` can still be read across regions. Exact regions such as `us-east-1`
need granular access, which Modal grants on request.

Also optional: `S3_PREFIX` (default `pdsh`), `S3_REGION` (default `us-east-1`),
`POLARS_BENCHMARK_REPO` (default `./polars-benchmark`), and `CACHE_VOLUME_NAME` /
`RESULTS_VOLUME_NAME` / `SEED_VOLUME_NAME` if the default Volume names collide.

## Usage

```bash
# Once, if you need the data: writes <prefix>/scale-<scale>/<table>.parquet for
# volume.py, cbm.py and mixed.py, and upstream's network layout,
# <prefix>/scale-factor-<scale>/1/<table>/*.parquet, for s3.py.
modal run prepare_data.py --scale 100.0

# All 22 queries. The first run fills the Volume, later runs hit the cache.
modal run volume.py

# A subset, and the same queries off the other input paths.
modal run volume.py --queries 1,6
modal run cbm.py --queries 1
modal run s3.py --queries 1

# Static tables cached, one fresh table read from the bucket on every scan.
modal run mixed.py --queries 3 --mode direct
```

Every run prints JSON per resource configuration: wall time, per-query seconds
from `timings.csv`, failed queries, the read speed of its input path, and the S3
bytes it moved, which for `volume.py` is the files the cache refresh copied and
is empty when nothing changed. Each entry point sweeps `CONFIGS` in `pdsh.py`,
one container per entry. Modal bills the CPU and memory a function requests
whether or not it uses them, so run the sweep before settling on a size.

Regenerate the charts with
`pip install -e '.[charts]' && python docs/make_charts.py` or
`uv sync --extra charts && uv run python docs/make_charts.py`.

## How to read these numbers

- Volume and direct-S3 bytes are measured, one as a file copy and the other from
  the container's own network counters. The mount's are not, because those
  counters stay near zero while it streams. Count those from the bucket side
  instead (CloudWatch `BytesDownloaded`).
- Dollars use $0.0000131 per core-second, $0.00000222 per GiB-second, a 1.75x
  multiplier on pinned rows, $0.09/GiB-month of Volume storage, and $0.09/GB
  internet egress where the container was off-region. Substitute your own egress
  rate and the shape holds.
- Read speed is a sequential pass over every Parquet file with eight parallel
  streams, so it measures the path under a small container, not a network
  ceiling. Cold-fill read speed is omitted because that probe measured the
  container's page cache.
- One container per run, not a cluster. Repeats varied 10% to 20% in wall time,
  so treat small differences as noise.

## Notes

- Upstream `pola-rs/polars-benchmark` is used unmodified. This repo supplies the
  container, the cache and the resource sizing. Per its README, results are not
  comparable to published TPC-H figures.
- `RUN_INCLUDE_IO=1` is set, so query timings include reading the Parquet, which
  is the whole point of comparing input paths.
- A `CloudBucketMount` is [built on Mountpoint for S3](https://modal.com/docs/guide/cloud-bucket-mounts),
  whose content cache Modal states is
  [not preserved across Function executions](https://github.com/modal-labs/modal-client/issues/1839),
  so a later run or another worker re-reads from the bucket. A Volume is durable
  and shared across workers.
- Runs are single-container. Polars' distributed engine spreads the same queries
  over many workers, where the input path matters more: each worker otherwise
  reads from the bucket independently, and worker-to-worker traffic over
  [i6pn](https://modal.com/docs/guide/i6pn) is same-region, so a distributed run
  is pinned in practice.

## License

Apache 2.0. See [`LICENSE`](LICENSE).
