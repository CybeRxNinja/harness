import { describe, it } from "node:test";
import assert from "node:assert/strict";

const { councilLines, councilArgs } = await import("../harness/plugin/harness.ts");

const DOC = {
  topic: "plan-auth",
  round: 2,
  seats: [
    {
      role: "panel-lead",
      pin: "kilo/nvidia/nemotron-3-ultra-550b-a55b:free#high",
      tier: "strong",
      free: true,
      strengths: ["reasoning", "code"],
      released: "2026-08-01",
      wins: 2,
      losses: 0,
      streak: 2,
      health: "ok",
    },
    {
      role: "panel-skeptic",
      pin: "opencode/beam-free#high",
      tier: "mid",
      strengths: ["review"],
      released: "",
      wins: 0,
      losses: 2,
      streak: -2,
      health: "failed",
      reason: "provider.quota: no credits",
    },
  ],
  retired: [
    { role: "panel-peer", pin: "acme/old-model", reason: "lost 2 in a row" },
  ],
  model_replaced: [
    { role: "panel-peer", old: "acme/old-model", new: "acme/new-model",
      reason: "retired by council vote" },
  ],
  collusion: [
    { pair: ["panel-peer", "panel-skeptic"], rounds: [1, 2],
      retired: "opencode/beam-free#high" },
  ],
  warnings: ["ignored vote 'x': no seat"],
};

describe("council formatting", () => {
  it("shows strengths, release, record, retirement and swaps", () => {
    const text = councilLines(DOC);
    assert.match(text, /council plan-auth · round 2 · 2 seat\(s\)/);
    assert.match(text, /reasoning\+code/);
    assert.match(text, /released 2026-08-01/);
    assert.match(text, /W2\/L0 streak \+2/);
    assert.match(text, /W0\/L2 streak -2/);
    assert.match(text, /NOT WORKING: provider\.quota/);
    assert.match(text, /retired panel-peer: acme\/old-model \(lost 2 in a row\)/);
    assert.match(text, /swapped panel-peer: acme\/old-model → acme\/new-model/);
    assert.match(text, /collusion: panel-peer \+ panel-skeptic voted for each other in 2 round\(s\)/);
    assert.match(text, /→ retired opencode\/beam-free#high/);
    assert.match(text, /warning: ignored vote/);
    assert.doesNotMatch(text, /undefined/);
  });

  it("tells the orchestrator how the survival round works", () => {
    const text = councilLines(DOC);
    assert.match(text, /never judges its own answer/);
    assert.match(text, /Two straight losses retire it/);
    assert.match(text, /voter>target=win\|loss\|tie/);
    assert.match(text, /collusion bloc/);
  });

  it("answers instead of throwing on an empty roster", () => {
    assert.match(councilLines({ seats: [] }), /council has no seats/);
    assert.match(councilLines({}), /council has no seats/);
  });
});

describe("council argv", () => {
  it("passes votes and puts the nargs=* roles flag last", () => {
    const args = councilArgs("/repo", {
      topic: "plan-auth",
      size: 2,
      votes: ["panel-lead>panel-peer=win", " "],
      roles: ["panel-lead", "panel-peer"],
    });
    assert.deepEqual(args.slice(0, 7),
      ["python3", "-m", "harness", "--root", "/repo", "models", "council"]);
    assert.ok(args.includes("--topic") && args.includes("plan-auth"));
    assert.ok(args.includes("--size") && args.includes("2"));
    assert.deepEqual(args.slice(args.indexOf("--vote"), args.indexOf("--vote") + 2),
      ["--vote", "panel-lead>panel-peer=win"]);
    assert.equal(args.at(-3), "--roles");
    assert.deepEqual(args.slice(-2), ["panel-lead", "panel-peer"]);
    assert.equal(args.filter((a) => a === "--vote").length, 1, "blank votes are dropped");
  });

  it("omits roles when none are asked for", () => {
    const args = councilArgs("/repo", { topic: "x" });
    assert.equal(args.includes("--roles"), false);
  });
});
