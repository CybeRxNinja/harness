import { describe, it } from "node:test";
import assert from "node:assert/strict";

const {
  detectSecretSpans,
  redactText,
  resolveSecret,
  redactToolResult,
  handleToolResult,
} = await import("../harness/plugin/harness.ts");

describe("Secret Broker v1", () => {
  it("detectSecretSpans finds tokens", () => {
    const spans = detectSecretSpans ? detectSecretSpans("sk-abc123def456") : [];
    assert.strictEqual(spans.length > 0, true, "should detect secret span");
  });

  it("redactText replaces secret with placeholder", () => {
    const result = redactText ? redactText("api_key: my-secret-key-1234", "session-1") : "";
    assert.strictEqual(typeof result, "string");
    assert.ok(result.includes("[[hb:"), "should contain placeholder");
    assert.ok(!result.includes("my-secret-key-1234"), "secret value removed");
  });

  it("redactText preserves env assignment key but replaces value only", () => {
    const text = "api_key=sk-abc123def45678901234";
    const result = redactText ? redactText(text, "session-1") : text;
    assert.strictEqual(result.includes("api_key="), true, "key preserved");
    assert.strictEqual(result.includes("sk-"), false, "secret prefix removed");
    assert.strictEqual(result.includes("[[hb:"), true, "value replaced");
  });

  it("redactText does not redact process.env references", () => {
    const text = "process.env.API_KEY";
    const result = redactText ? redactText(text, "session-1") : text;
    assert.strictEqual(result.includes("[[hb:"), false, "process.env should not be redacted");
  });

  it("redactText removes PEM whole block", () => {
    const pem = "-----BEGIN RSA PRIVATE KEY-----\nMII...\n-----END RSA PRIVATE KEY-----";
    const result = redactText ? redactText(pem, "session-1") : pem;
    assert.strictEqual(result.includes("-----BEGIN"), false, "PEM header removed");
    assert.strictEqual(result.includes("-----END"), false, "PEM footer removed");
  });

  it("redactText replaces Bearer token but keeps Bearer word", () => {
    const text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcd1234efgh5678";
    const result = redactText ? redactText(text, "session-1") : text;
    assert.strictEqual(result.includes("Bearer"), true, "Bearer word kept");
    assert.strictEqual(result.includes("eyJhbGci"), false, "token removed");
    assert.strictEqual(result.includes("[[hb:"), true, "token replaced with placeholder");
  });

  it("redactText is idempotent (same placeholder on second pass, no nesting)", () => {
    const text = "sk-testtoken12345678";
    const r1 = redactText ? redactText(text, "session-1") : "";
    const r2 = redactText ? redactText(r1, "session-1") : "";
    assert.strictEqual(r1, r2, "second redact should leave placeholder unchanged");
    assert.strictEqual((r2.match(/\[\[hb:[a-f0-9]{8}-\d+\]\]/g) || []).length, 1, "exactly one placeholder");
  });

  it("redactText multi-secret text has two placeholders and no leftover token chars", () => {
    const text = "sk-abc123def456 and ghp_abc123def456";
    const result = redactText ? redactText(text, "session-1") : text;
    const matches = result.match(/\[\[hb:[a-f0-9]{8}-\d+\]\]/g) || [];
    assert.strictEqual(matches.length, 2, "two placeholders for two secrets");
    assert.strictEqual(new Set(matches).size, 2, "distinct placeholders");
    assert.strictEqual(result.includes("sk-"), false, "no leftover sk- prefix");
    assert.strictEqual(result.includes("ghp_"), false, "no leftover ghp_ prefix");
    assert.strictEqual(result.includes("abc123def456"), false, "no leftover token chars");
  });

  it("redactText two secrets with surrounding text keeps context intact", () => {
    const text = "key1=sk-abc123def456 key2=ghp_abc123def456 end";
    const result = redactText ? redactText(text, "session-1") : text;
    assert.strictEqual(result.includes("key1="), true, "key name preserved");
    assert.strictEqual(result.includes("key2="), true, "second key name preserved");
    assert.strictEqual(result.includes(" end"), true, "trailing text preserved");
    assert.strictEqual(result.includes("sk-"), false, "first secret removed");
    assert.strictEqual(result.includes("ghp_"), false, "second secret removed");
  });

  it("resolveSecret returns original fake value for same session", () => {
    const text = "sk-testtoken12345678";
    const redacted = redactText ? redactText(text, "session-1") : text;
    const placeholder = redacted.match(/\[\[hb:[a-f0-9]{8}-\d+\]\]/)?.[0] ?? "";
    const resolved = resolveSecret ? resolveSecret(placeholder, "session-1") : null;
    assert.strictEqual(resolved, "sk-testtoken12345678", "same-session resolve returns original fake value");
  });

  it("resolveSecret returns null for cross-session", () => {
    const placeholder = "[[hb:deadbeef-1]]";
    const resolved = resolveSecret ? resolveSecret(placeholder, "other-session") : null;
    assert.strictEqual(resolved, null, "cross-session resolve returns null");
  });

  it("resolveSecret does not create vault on miss", () => {
    const resolved = resolveSecret ? resolveSecret("[[hb:nonexistent-99]]", "new-sess") : null;
    assert.strictEqual(resolved, null);
  });

  it("repeated secret gets same placeholder within session", () => {
    const text = "api_key=sk-testtoken12345678 and api_key=sk-testtoken12345678";
    const result = redactText ? redactText(text, "session-1") : text;
    const matches = result.match(/\[\[hb:[a-f0-9]{8}-\d+\]\]/g) || [];
    assert.strictEqual(matches.length, 2, "exactly two placeholder occurrences");
    assert.strictEqual(new Set(matches).size, 1, "same placeholder for same secret");
  });

  it("handleToolResult redacts error event that condense skips", () => {
    const event = { status: "error", result: { content: [{ type: "text", text: "ghp_abc123def456 error output" }] } };
    handleToolResult ? handleToolResult(event, "session-1") : null;
    const text = event.result?.content?.[0]?.text ?? "";
    assert.strictEqual(text.includes("ghp_"), false, "secret prefix removed from error");
    assert.strictEqual(text.includes("[[hb:"), true, "placeholder present in error");
  });

  it("redactToolResult redacts error status and short text (<4000)", () => {
    const event = { status: "error", result: { content: [{ type: "text", text: "sk-abc123def456 short" }] } };
    redactToolResult ? redactToolResult(event, "session-1") : null;
    const text = event.result?.content?.[0]?.text ?? "";
    assert.strictEqual(text.includes("sk-"), false, "secret prefix removed");
    assert.strictEqual(text.includes("[[hb:"), true, "placeholder present");
  });

  it("redactText skips spans overlapping existing placeholders and does not nest", () => {
    const text = "some [[hb:deadbeef-1]] value";
    const result = redactText ? redactText(text, "session-1") : text;
    assert.strictEqual(result.includes("[[hb:deadbeef-1]]"), true, "existing placeholder preserved");
    const placeholders = result.match(/\[\[hb:[a-f0-9]{8}-\d+\]\]/g) || [];
    assert.strictEqual(placeholders.length, 1, "only one placeholder, no nesting");
  });

  it("env assignment does not nest when value is already a placeholder", () => {
    const text = "SECRET=[[hb:deadbeef-1]]";
    const result = redactText ? redactText(text, "session-1") : text;
    const placeholders = result.match(/\[\[hb:[a-f0-9]{8}-\d+\]\]/g) || [];
    assert.strictEqual(placeholders.length, 1, "no nesting on placeholder value");
  });
});
