import { describe, it } from "node:test";
import assert from "node:assert/strict";

const { modelsStatusLines, autosyncGate, routeLines } = await import(
  "../harness/plugin/harness.ts"
);

const STATUS = {
  counts: { available: 10, free: 3, verified: 2, strong_free_verified: 1 },
  sync: { age_s: 120, stale: false, last_error: "" },
  recent: [
    {
      model: "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
      pin: "kilo/nvidia/nemotron-3-ultra-550b-a55b:free#high",
      tier: "strong",
      free: true,
      health: "ok",
    },
    {
      model: "opencode/withdrawn-free",
      pin: "opencode/withdrawn-free#max",
      tier: "strong",
      free: true,
      health: "failed",
      reason: "provider.no-route: Model unavailable",
    },
  ],
  providers: [
    {
      provider: "kilo",
      quota: { exhausted: true, remaining_day: 0, note: "free: 200 requests/hour/IP" },
    },
  ],
  warnings: ["no verified strong free model — free strong work may fail"],
};

describe("models_status formatting", () => {
  it("prints counts, strong-free pins, failures, quota and warnings", () => {
    const text = modelsStatusLines(STATUS);
    assert.match(text, /10 available, 3 free, 2 verified, 1 strong-free-verified/);
    assert.match(text, /strong-free-verified: kilo\/nvidia\/nemotron/);
    assert.match(text, /recent failures: opencode\/withdrawn-free: provider\.no-route/);
    assert.match(text, /quota kilo/);
    assert.match(text, /warning: no verified strong free model/);
    assert.doesNotMatch(text, /undefined/);
  });

  it("never prints undefined for an empty status and caps at 15 lines", () => {
    const text = modelsStatusLines({ counts: {}, sync: {}, recent: [], providers: [], warnings: Array(30).fill("w") });
    assert.doesNotMatch(text, /undefined/);
    assert.ok(text.split("\n").length <= 15);
  });

  it("handles a missing response", () => {
    assert.equal(modelsStatusLines(null), "models_status: empty response");
  });
});

describe("autosyncGate", () => {
  const fresh = () => ({ sessions: new Set(), lastNoSession: 0 });

  it("runs once per session id, then never again for that id", () => {
    const state = fresh();
    assert.equal(autosyncGate("ses_1", state, 1000, 300000), true);
    assert.equal(autosyncGate("ses_1", state, 2000, 300000), false);
    assert.equal(autosyncGate("ses_2", state, 3000, 300000), true);
  });

  it("with no session id, runs at most once per TTL", () => {
    const state = fresh();
    assert.equal(autosyncGate("", state, 1000, 300000), true);
    assert.equal(autosyncGate(undefined, state, 2000, 300000), false);
    assert.equal(autosyncGate(undefined, state, 1000 + 300000, 300000), true);
  });

  it("caps the session set at 200 and evicts the oldest", () => {
    const state = fresh();
    for (let i = 0; i < 200; i++) assert.equal(autosyncGate(`ses_${i}`, state, i, 1), true);
    assert.equal(state.sessions.size, 200);
    assert.equal(autosyncGate("ses_new", state, 500, 1), true);
    assert.equal(state.sessions.size, 200);
    assert.equal(state.sessions.has("ses_0"), false);
    assert.equal(state.sessions.has("ses_new"), true);
  });
});

describe("models_route formatting", () => {
  it("appends a quota note when a row carries one", () => {
    const text = routeLines([
      {
        role: "deep",
        pin: "kilo/nvidia/nemotron:free#high",
        tier: "strong",
        free: true,
        strengths: ["reasoning", "code"],
        released: "2026-08-01",
        health: "ok",
        quota_note: "2/200 rph",
      },
    ]);
    assert.match(text, /reasoning\+code/);
    assert.match(text, /released 2026-08-01/);
    assert.match(text, /quota 2\/200 rph/);
    assert.match(text, /working/);
  });

  it("keeps the failed and unchecked verdicts", () => {
    const text = routeLines([
      { role: "deep", pin: "acme/x#high", tier: "strong", health: "failed", reason: "provider.quota: no credits" },
      { role: "explore", pin: "freeco/tiny-free#low", tier: "cheap", health: "unverified", free: true },
    ]);
    assert.match(text, /NOT WORKING: provider\.quota/);
    assert.match(text, /unchecked/);
    assert.match(text, /Do not dispatch: deep/);
  });

  it("says so when nothing is routed", () => {
    assert.match(routeLines([]), /no routed roles/);
  });
});
