import { describe, it } from "node:test";
import assert from "node:assert/strict";

const { skillFrontmatter, skillTargetPath } = await import(
  "../harness/plugin/harness.ts"
);

describe("skill frontmatter", () => {
  it("folds a block scalar instead of indexing the literal '>-'", () => {
    const fm = skillFrontmatter(
      "---\nname: x\ndescription: >-\n  Use when the parser\n  rejects a block scalar.\n---\nbody"
    );
    assert.equal(fm.name, "x");
    assert.equal(fm.description, "Use when the parser rejects a block scalar.");
  });

  it("strips matching quotes and keeps an inline value", () => {
    const fm = skillFrontmatter('---\nname: y\ndescription: "a: b"\n---\n');
    assert.equal(fm.description, "a: b");
    assert.equal(skillFrontmatter("no frontmatter").name, undefined);
  });
});

describe("skill path boundary", () => {
  it("accepts files inside the skill dir and rejects escapes", () => {
    assert.equal(skillTargetPath("/a/b/skill", "references/x.md"),
      "/a/b/skill/references/x.md");
    assert.equal(skillTargetPath("/a/b/skill", "./references/../x.md"),
      "/a/b/skill/x.md");
    assert.equal(skillTargetPath("/a/b/skill", "../skill-evil/x"), "",
      "a sibling sharing the name prefix is outside the skill");
    assert.equal(skillTargetPath("/a/b/skill", "/etc/passwd"), "");
    assert.equal(skillTargetPath("/a/b/skill", ".."), "");
    assert.equal(skillTargetPath("/a/b/skill", ""), "");
  });
});
