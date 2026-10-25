import { describe, it } from "node:test";
import assert from "node:assert/strict";

const {
  redactText,
  resolveSecretMeta,
  substituteRef,
  prepareSecretExec,
} = await import("../harness/plugin/harness.ts");

const SID = "ses_secret_exec_test";
const REF_RE = /\[\[hb:[a-f0-9]{8}-\d+\]\]/;

function placeholderFor(text, sid = SID) {
  const out = redactText(text, sid);
  const m = out.match(REF_RE);
  assert.ok(m, `expected a placeholder in ${out}`);
  return { ref: m[0], text: out };
}

describe("Secret references carry their env key", () => {
  it("GITHUB_TOKEN=value yields that env key", () => {
    const { ref } = placeholderFor("GITHUB_TOKEN=ghp_aaa111bbb222");
    const meta = resolveSecretMeta(ref, SID);
    assert.ok(meta);
    assert.equal(meta.envKey, "GITHUB_TOKEN");
    assert.equal(meta.value, "ghp_aaa111bbb222");
  });

  it("a prefix-only token has no env key", () => {
    const { ref } = placeholderFor("ghp_ccc333ddd444");
    const meta = resolveSecretMeta(ref, SID);
    assert.ok(meta);
    assert.equal(meta.envKey, "");
  });

  it("lookup is session-keyed and never crosses sessions", () => {
    const { ref } = placeholderFor("GITHUB_TOKEN=ghp_aaa111bbb222");
    assert.equal(resolveSecretMeta(ref, "ses_other"), null);
    assert.equal(resolveSecretMeta("not-a-ref", SID), null);
  });
});

describe("substitution and derived forms", () => {
  it("substitutes literally and keeps shell syntax in one element", () => {
    const { ref } = placeholderFor("TOKEN=abc123def456");
    const argv = ["curl", "-H", `Authorization: Bearer ${ref}`, "https://api.github.com"];
    const out = substituteRef(argv, ref, "s3cret; rm -rf /");
    assert.equal(out.length, argv.length);
    assert.equal(out[2], "Authorization: Bearer s3cret; rm -rf /");
  });

  it("redacts a base64 copy to the same placeholder", () => {
    const secret = "sk-abc123def456";
    const { ref } = placeholderFor(secret);
    const encoded = Buffer.from(secret, "utf8").toString("base64");
    assert.ok(encoded !== secret);
    const out = redactText(`payload=${encoded}`, SID);
    assert.ok(out.includes(ref));
    assert.ok(!out.includes(encoded));
  });

  it("stays idempotent after the derived-form pass", () => {
    const { text } = placeholderFor("sk-abc123def456");
    assert.equal(redactText(text, SID), text);
  });
});

describe("prepareSecretExec denials", () => {
  const auth = (ref) => ["curl", "-H", `Authorization: Bearer ${ref}`, "https://api.github.com"];

  it("denies a reference with no env key", async () => {
    const { ref } = placeholderFor("ghp_ccc333ddd444");
    const out = await prepareSecretExec({ ref, argv: auth(ref) }, SID);
    assert.equal(out.ok, false);
    assert.match(out.reason, /env key/);
  });

  it("denies a non-curl program", async () => {
    const { ref } = placeholderFor("GITHUB_TOKEN=ghp_bbb222ccc333");
    const out = await prepareSecretExec({ ref, argv: ["wget", auth(ref)[2], "https://api.github.com"] }, SID);
    assert.equal(out.ok, false);
    assert.match(out.reason, /curl/);
  });

  it("denies a reference that occurs zero or twice", async () => {
    const { ref } = placeholderFor("GITHUB_TOKEN=ghp_ddd444eee555");
    const zero = await prepareSecretExec({ ref, argv: ["curl", "https://api.github.com"] }, SID);
    assert.equal(zero.ok, false);
    const twice = await prepareSecretExec(
      { ref, argv: ["curl", "-H", `Authorization: Bearer ${ref} ${ref}`, "https://api.github.com"] },
      SID,
    );
    assert.equal(twice.ok, false);
    assert.match(twice.reason, /exactly once/);
  });

  it("denies control characters and unknown refs", async () => {
    const { ref } = placeholderFor("GITHUB_TOKEN=ghp_fff666aaa777");
    const control = await prepareSecretExec({ ref, argv: auth(ref).concat("x\ny") }, SID);
    assert.equal(control.ok, false);
    assert.match(control.reason, /control/);
    const unknown = await prepareSecretExec(
      { ref: "[[hb:deadbeef-1]]", argv: auth("[[hb:deadbeef-1]]") },
      SID,
    );
    assert.equal(unknown.ok, false);
    assert.match(unknown.reason, /unknown secret reference/);
  });

  it("accepts a valid request when curl exists", async () => {
    if (process.platform !== "linux") return;
    const { ref } = placeholderFor("GITHUB_TOKEN=ghp_aaa111bbb222");
    const out = await prepareSecretExec({ ref, argv: auth(ref) }, SID);
    assert.equal(out.ok, true);
    assert.equal(out.envKey, "GITHUB_TOKEN");
    assert.equal(out.value, "ghp_aaa111bbb222");
    assert.ok(out.curlPath.startsWith("/"));
  });
});
