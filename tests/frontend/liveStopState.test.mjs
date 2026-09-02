/**
 * Unit tests for readStopState — the banner's "is the desk stopped?" verdict.
 *
 * Run: node tests/frontend/liveStopState.test.mjs
 *
 * These exercise the real function under node rather than grepping the JSX,
 * because the defect being guarded here was a LOGIC one: the banner asked only
 * `blocked_until_reset === true`, so a kill-switch engine halt (which does set
 * the latch, but whose reason lives on the halt) and every halt that does NOT
 * touch the latch rendered nothing at all.
 */
import assert from "node:assert/strict";
import { readStopState } from "../../frontend/src/lib/liveStopState.js";

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed += 1;
  } catch (err) {
    console.error(`FAIL: ${name}\n  ${err.message}`);
    process.exitCode = 1;
  }
}

const CLEAR = {
  blocked_until_reset: false,
  latched_at: null,
  latched_reason: null,
  engine_halted: false,
  engine_halted_at: null,
  engine_halt_reason: null,
};

test("a clear desk renders no banner", () => {
  assert.equal(readStopState(CLEAR).stopped, false);
});

test("null/undefined config never invents a stop", () => {
  assert.equal(readStopState(null).stopped, false);
  assert.equal(readStopState(undefined).stopped, false);
  assert.equal(readStopState({}).stopped, false);
});

test("THE INCIDENT: an engine halt with the latch clear still shows", () => {
  // This is the exact state after kill switch -> reset-latch, under the old
  // code: latch false, halt still set. The banner used to render nothing.
  const s = readStopState({
    ...CLEAR,
    engine_halted: true,
    engine_halt_reason: "kill_switch",
    engine_halted_at: "2026-09-02T04:36:40Z",
  });
  assert.equal(s.stopped, true, "an engine halt must be visible on its own");
  assert.equal(s.reason, "kill_switch");
  assert.equal(s.at, "2026-09-02T04:36:40Z");
  assert.equal(s.title, "Live trading stopped — engine halted");
});

test("a latch alone still shows, with the latch wording", () => {
  const s = readStopState({
    ...CLEAR,
    blocked_until_reset: true,
    latched_reason: "broker_stop_loss",
    latched_at: "2026-09-02T04:00:00Z",
  });
  assert.equal(s.stopped, true);
  assert.equal(s.reason, "broker_stop_loss");
  assert.equal(s.title, "Live trading halted — safety latch tripped");
});

test("both set (a real kill switch) prefers the halt's specific reason", () => {
  const s = readStopState({
    blocked_until_reset: true,
    latched_reason: "kill_switch",
    latched_at: "2026-09-02T04:36:39Z",
    engine_halted: true,
    engine_halt_reason: "reconcile_mismatch",
    engine_halted_at: "2026-09-02T04:36:40Z",
  });
  assert.equal(s.stopped, true);
  assert.equal(s.reason, "reconcile_mismatch", "the halt carries the real cause");
  assert.equal(s.at, "2026-09-02T04:36:40Z");
  assert.equal(s.title, "Live trading stopped — kill switch used");
});

test("a halt with no recorded reason says so rather than inventing one", () => {
  const s = readStopState({ ...CLEAR, engine_halted: true });
  assert.equal(s.stopped, true);
  assert.equal(s.reason, "unspecified");
  assert.equal(s.at, null);
});

test("only a literal true counts as stopped — no truthy coercion", () => {
  for (const v of [1, "true", "yes", [1], {}]) {
    assert.equal(readStopState({ ...CLEAR, engine_halted: v }).stopped, false,
      `engine_halted=${JSON.stringify(v)} must not read as a stop`);
    assert.equal(readStopState({ ...CLEAR, blocked_until_reset: v }).stopped, false,
      `blocked_until_reset=${JSON.stringify(v)} must not read as a stop`);
  }
});

test("a stale poll that dropped the halt keys does not clear a live latch", () => {
  const s = readStopState({ blocked_until_reset: true, latched_reason: "profit_lock" });
  assert.equal(s.stopped, true);
  assert.equal(s.reason, "profit_lock");
});

if (process.exitCode) {
  console.error(`\n${passed} passed, some FAILED`);
} else {
  console.log(`${passed} passed`);
}
