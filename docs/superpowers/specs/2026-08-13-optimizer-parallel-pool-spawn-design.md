# Optimizer parallel pool: survive a broken pool, and stop forking a threaded server

**Date:** 2026-08-13
**Status:** IMPLEMENTED 2026-08-13. Verified on the live server with the exact
failing config (`confluence_scalper` / NIFTY / `option_rerank` / 6 workers):
`parallel pool: 6 spawn workers ready in 1.4s (rows=55875)`. Spawn startup cost
is 1.4s once per job — the risk flagged in §7 measured and closed.
**Area:** `backend/app/parallel_eval.py`, `backend/app/optimizer.py`

## 1. The failure

Every optimizer job with `opt_workers > 1` fails within ~10 seconds of starting:

```
concurrent.futures.process.BrokenProcessPool: A process in the process pool was
terminated abruptly while the future was running or pending.
```

Reported for `confluence_scalper · SENSEX · net_pnl_inr · 2026-08-12 23:28:57`
(job `1c2da59d`), and reproduced on jobs `bd9e8688` and `78890cd5`. It is **not
instrument-specific**: an identical 10-trial NIFTY job at `opt_workers: 6` failed
the same way. SENSEX was simply the first parallel run attempted since the
breakage — the last 11 successful 6-worker runs were all 2026-07-29 → 07-31.

## 2. Root cause

`start_pool` forks worker processes out of the live uvicorn server, which runs
~31 threads. Forking a heavily-threaded process is unsafe, and here the children
die immediately. The WSL2 kernel log during a failure:

```
uvicorn[498482]: segfault at 78e7eaffd990 ... error 4 in libc.so.6
python3.11: uvicorn: potentially unexpected fatal signal 11
WSL (CaptureCrash): Capturing crash for pid: 1260, executable: python3.11, signal: 11
```

The faulting instruction is `mov eax,[rdi+0x2d0]` — `struct pthread->tid` in
glibc. The forked children dereference a thread structure that did not survive
the fork. They die on the **no-op warmup task** at `parallel_eval.py:151`,
before a single trial runs.

Ruled out with evidence:

| Hypothesis | Evidence against |
|---|---|
| Memory / OOM | No container limit; 398 MiB of 15.4 GiB; `memory.events oom_kill 0` |
| PID limit | `pids.max = max` |
| `/dev/shm` exhaustion | 64 MB, 20 KB used |
| Overcommit refusing fork | `vm.overcommit_memory = 1` (always overcommit) |
| SENSEX data volume | NIFTY and SENSEX both have **55,875** candles in the job window |
| Import-level regression | A container process that imports `server` forks 15 workers fine |
| Child reaper stealing exit status | No `SIGCHLD` handler (`SigCgt` = SIGINT, SIGTERM, RT33 only) |
| Fork-hostile native lib (grpc) | Not installed |

A clean process forks fine; a process that imports the whole app forks fine;
only the *running server* fails. The fork was always unsafe — it was winning a
race it was eventually going to lose.

### 2.1 What is NOT the cause (tested, not assumed)

The long-running server fails 100% of the time. A **freshly started container**
running the **exact same code** completes 6-worker jobs (10/10 trials, reached
the analyzing stage). Two further candidates were tested and eliminated:

- **Recent commits.** `parallel_eval.py` last changed 2026-08-01 00:48 IST and
  `optimizer.py` 2026-08-01 00:38 IST — both *before* the last successful
  6-worker run (2026-07-31 19:51 UTC). Everything committed since is paper
  trading, warehouse, live-risk, readiness and docs. Today's exact working tree
  runs the pool successfully on a fresh process.
- **The rebuilt base image.** `backend/Dockerfile` pins the floating tag
  `FROM python:3.11-slim`. The image was rebuilt 2026-08-12T17:47:33Z — minutes
  before the first failure — pulling a CPython built Aug 5 2026 in place of the
  Jun 24 2026 build the successful runs used (glibc identical at 2.41). This
  looked decisive, so it was A/B tested: fresh containers on **both** builds run
  6-worker jobs successfully. The interpreter build is not the cause.

Also eliminated: thread count (40 idle threads fork fine), rapid thread
create/destroy churn, `uvloop` (not installed), loading the real 55,875-row
frame, and `evaluation_mode` (a `spot` job fails on the poisoned process too).

A second round of elimination compared the poisoned server against healthy
fresh containers directly:

- **Identical native code.** `/proc/1/maps` lists exactly the same `.so` files
  in both — zero extra libraries in the poisoned process. No BLAS/OpenMP/libcurl
  difference.
- **Identical thread composition.** py-spy shows only the same *kinds* of
  threads, in different counts (poisoned: 1 main + 15 `asyncio_N` + 10
  `ThreadPoolExecutor-0_N` + 3 pymongo = 29; healthy: 12). No broker/feed thread,
  and **no leaked pool threads** — so failed attempts do not self-poison.
- **Not container config.** A probe with the same v9fs plugins bind mount runs
  6-worker jobs fine; a fresh probe runs the same job three times in a row with
  no thread growth.

The one difference not testable in isolation is that the live container is the
only one with `LIVE_AUTOPLACE_ARMED` / `LIVE_GUARD_ARMED` and Flattrade
credentials. Replicating that would mean running a second armed backend against
the real account, which is not an acceptable experiment. It stays unresolved by
choice, and the fix does not depend on it.

What remains unidentified is the specific runtime activity that poisons the
server process. That gap does not block this design — the fix removes the
hazard class rather than the trigger — but it means **restarting the backend is
a workaround, not a cure**, and the first failure came only ~9 minutes after a
fresh boot.

Separately worth fixing: the unpinned `python:3.11-slim` base silently changed
the interpreter build under a routine rebuild. It did not cause this, but it
makes future incidents harder to reason about.

## 3. Scope

**In scope:** make a broken pool non-fatal, and make parallel evaluation work
again from a threaded server process.

**Out of scope:** moving optimization into a dedicated worker process (considered
and deferred — cleanest long-term, but a real restructure across job lifecycle,
cancel/pause, and progress reporting); changing the default `opt_workers`
(stays 1); changing search behaviour or determinism.

## 4. Design

### Part 1 — Safety net: a broken pool degrades, it does not kill the job

`start_pool`'s eager warmup already *detects* an unusable pool. Today it throws,
and the exception propagates out of `run_optimization` and fails the job.

Change: wrap the warmup. On `BrokenProcessPool` — or any exception — shut the
pool down, clear the `_POOL` and `_RAW_DF` module globals so the next job is not
poisoned by a half-built pool, log a warning, and return `None`. `None` is
already the module's established "caller runs sequentially" signal, handled by
`parallel_backtest`, so no call-site logic changes.

`run_optimization` additionally sets a **job-level warning** when the pool was
requested but unavailable, using the existing `_update_job(job_id, {"warning": …})`
precedent (`optimizer.py:1688`):

> `parallel workers unavailable — ran sequentially (results are correct; expect
> roughly the requested worker count as a slowdown factor)`

The job completes with correct results. It is never silently slow and never
silently wrong.

### Part 2 — Switch the pool from `fork` to `spawn`

Workers become fresh interpreters with no inherited thread state, which makes
this failure class structurally impossible regardless of how many threads the
server grows.

- `ctx = multiprocessing.get_context("spawn")`.
- `raw_df` moves from COW-inheritance to `initargs` — pickled **once per worker
  at pool construction**, not per task. Per-task payloads
  (`sid, merged, slice_bounds`) are unchanged, so per-trial cost is unchanged.
- `_init_worker(raw_df)` sets `_RAW_DF`, resets `_WORKER_CACHES = {}`, and calls
  `get_registry().auto_discover()`.
- `effective_workers` drops its `fork_available()` gate — spawn is available on
  every platform, including the Windows host used for tests. The
  `AF_OPT_WORKERS` env cap and the `cpu - 1` clamp stay.

**Why `auto_discover()` is load-bearing.** The registry does not self-populate;
`server.py:71` populates it at startup and a spawn worker does not inherit that.
`_worker_evaluate` swallows all exceptions and returns `(None, merged)`, so a
worker with an empty registry would score **every trial as a failure** and the
job would complete with garbage. Plugin strategies are file-backed under
`app/strategies/plugins/`, so rediscovery in the worker recovers them.

**Fail-fast warmup.** Replace the `_noop` warmup with a task that asserts the
worker's registry resolves the `strategy_id` being optimized. If it cannot, the
worker raises, the pool breaks loudly, and Part 1 routes the job to the
sequential path — which produces correct results. It must never be possible to
record 800 silent zeros.

### Part 3 — Docstrings

The module docstring promises "raw_df is COW-inherited via fork (never pickled)"
and `start_pool`'s docstring describes forking workers eagerly to snapshot
`_RAW_DF` before reassignment. Both become false and must be rewritten, not left
as traps. The `_POOL_LOCK` single-active-pool invariant stays; the `_RAW_DF`
reassignment race it also guarded disappears under spawn, because each worker
receives its own copy at construction.

## 5. Behaviour contract

| Condition | Result |
|---|---|
| `opt_workers <= 1` | Sequential, unchanged, byte-identical to today |
| `opt_workers > 1`, pool starts | Parallel, results equivalent to sequential |
| `opt_workers > 1`, pool broken | Sequential + job-level warning; job completes |
| Second parallel job while one is active | `None` → sequential, as today |
| Worker registry empty | Warmup fails → sequential; never silent zeros |

## 6. Test plan

Update `tests/test_parallel_eval.py` and `tests/test_parallel_eval_wfo.py`,
which currently assert fork behaviour —
`test_effective_workers_clamps_and_falls_back` and
`test_start_pool_returns_none_without_fork` both monkeypatch `fork_available`
and must be rewritten around the spawn contract.

New coverage:

1. Warmup failure returns `None` rather than raising, and leaves `_POOL` /
   `_RAW_DF` clear so a subsequent job can build a fresh pool.
2. A spawn worker's registry resolves a known strategy id (guards the
   silent-zeros regression directly).
3. Sequential and parallel paths agree on metrics for a fixed param set —
   following the existing `tests/test_indicator_equivalence.py` pattern.
4. `run_optimization` records the job-level warning when the pool is
   unavailable.

## 7. Risks and open questions

- **Spawn startup cost is unmeasured.** Each worker re-imports
  `app.parallel_eval` and its dependencies and runs `auto_discover()`. Expected
  to be seconds, paid once per job, against an 800-trial run — but it must be
  measured during implementation, and logged, not assumed.
- **`raw_df` pickle size is unmeasured.** ~55k rows; expected single-digit MB
  per worker. Measure and log.
- **The runtime activity that poisons the server process is unidentified.** The
  mechanism is confirmed and code / image / thread-count / churn have all been
  eliminated by test (§2.1). We cannot claim "regression introduced in X" —
  because, as far as the evidence goes, no change introduced it.
- Verification must include a real end-to-end run from the UI at
  `opt_workers = 6`, not just tests — the failure only reproduces from the live
  threaded server.

## 8. Immediate workaround (available now, no code change)

Set **Workers = 1** in the optimizer setup. The sequential path is untouched by
this bug.
