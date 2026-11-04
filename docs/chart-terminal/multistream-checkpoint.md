# Shared chart stream checkpoint

Phase 2 source work; synthetic verification only. No production rebuild, process
restart, broker request, order mutation or broker session action was performed.

## Pieces and checks

1. Query validation — done when malformed shapes/timeframes and more than six
   distinct channels fail before any producer starts. Duplicate channels collapse.
2. Server fanout — done when channel identity survives multiplexing, a failed
   channel leaves other channels alive, and closing/cancelling releases every
   subscriber and chart lease.
3. Shared browser transport — done when same-channel consumers share ownership,
   registrations batch, changed subscriptions replace one connection, stale
   events are ignored, and the final close releases the connection.
4. Synthetic fixture — done when six channel snapshots can be read from one
   iterator with separate NIFTY/BANKNIFTY/SENSEX synthetic prices and weekly bars.

## Implementation

- `/api/charts/streams?channels=<JSON array>` accepts up to six distinct
  `{instrument_key,timeframe}` channels. Syntax/timeframe failures reject the
  query. Every instrument is resolved before producers start; an unsupported or
  expired instrument yields its own `stream_error`, preserving other charts.
- Existing `/api/charts/stream` remains available.
- `chart_multi_event_stream` merges existing channel generators through a
  bounded queue. It backpressures chart producers without dropping their bars.
  The independent market-manager queues remain nonblocking; upstream tick loss
  invokes the existing `resync` and REST reseeding behavior. Only the parent
  reads ASGI disconnect state. Cancellation awaits all generator cleanup.
- `chartApi.stream` returns a consumer wrapper from one page-local stream pool.
  It supports listeners, `onopen`/`onerror`, `readyState` and `close`. Identical
  channels share the server subscription. Channel-set changes replace the
  transport; old connection callbacks are ignored. Joining an already-open
  channel reports open asynchronously. Constructor errors notify consumers and
  leave the same subscription set retryable by a later registration.
- Fixture adds `/api/charts/streams`, BANKNIFTY/SENSEX metadata, distinct
  synthetic index prices and weekly intervals. Every payload remains marked as
  synthetic; none is market acceptance evidence.

## Verification recorded by stream agent

- Red checks observed missing backend validator/generator/route and missing
  frontend module before implementation. Revised checks then observed the
  metadata-isolation/open-join/constructor-error failures before those fixes.
- `python -m pytest tests/test_chart_multistream.py tests/test_chart_terminal.py tests/test_chart_stream_leases.py -q`
  — **28 passed**.
- `CI=true npm test -- --watchAll=false --runInBand --runTestsByPath src/lib/chartStreamPool.test.js`
  in `frontend` — **6 passed**. Existing optional visual-edits plugin notices
  appeared; they are not test failures.
- Direct fixture import and six-event iteration using `.venv/Scripts/python.exe`
  — **passed**: all requested identities, three distinct synthetic prices,
  weekly interval, iterator closed.
- `python -m py_compile backend/app/chart_stream.py backend/app/routers/charts.py tests/manual/chart_terminal_fixture.py`
  and scoped `git diff --check` — **passed**. Git reported its existing LF/CRLF
  conversion notices.
- System Python fixture import could not run because `pymongo` is absent;
  rerun with project `.venv` passed. Use the project interpreter for fixture
  launch. The already-running fixture was left untouched for root to restart.

## Critical risk and remaining checks

Critical part: a slow multiplexed client must preserve candle extrema or
explicitly resync. Checked independently with a one-slot transport queue (all
finite source candle highs retained) and real manager queue overflow (resync
followed by REST-seeded high/low retained). No broker or socket connection was
needed for either check.

Browser proof of one physical EventSource with six chart tiles and available
REST requests remains the root agent's fixture acceptance step. Production
deployment and real moving-market acceptance are outside this checkpoint.

Core lesson: share the HTTP transport while retaining channel ownership and
loss detection. Confirmed approach: bounded backpressure plus existing upstream
overflow resync preserves correctness. Dead end: rejecting the entire shared
request for one expired saved instrument would disable otherwise valid tiles.

## Final review agent — transient channel recovery

Confirmed: the initial multiplexing implementation left a channel permanently
stopped after a transient history/subscription error while healthy siblings kept
the shared HTTP connection open. Reproduced with the actual in-memory market
manager: the failed history loader was called once, followed only by healthy
channel heartbeats. No network connection or broker action was used.

The channel pump now retries error termination with exponential delays from
1 second to a 30-second cap. It closes each failed generator before retrying,
retains healthy siblings, and emits a channel-scoped recovery `resync` before the
first recovered snapshot so browser history fills any missed interval. Closing
or cancelling the shared stream also cancels pending retry sleeps.

Verification: new recovery tests first failed with a timeout awaiting the missing
recovered snapshot. After the fix,
`.venv/Scripts/python -m pytest tests/test_chart_multistream.py tests/test_chart_terminal.py tests/test_chart_stream_leases.py -q`
reported **33 passed** with the existing `python_multipart` deprecation warning.
The recovery tests verify fresh ticks for both channels and unchanged healthy
lease ownership. Additional tests verify capped backoff, release before retry,
and no leftover tasks/leases when closing or cancelling during retry sleep.

Core lesson: isolating producer failure also requires producer recovery when a
shared connection stays open. Confirmed approach: retry one channel after its
cleanup, then request history resync. Dead end: relying on browser EventSource
reconnect cannot recover one failed producer while its siblings remain active.
