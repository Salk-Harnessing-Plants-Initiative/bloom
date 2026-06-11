import { describe, it, expect } from "vitest";
import { isFamilyMonophyletic } from "./monophyly";
import type { TreeNode } from "./types";

// Convenience builders.
const leaf = (name: string, distance?: number): TreeNode => ({
  name,
  distance,
});
const node = (...children: TreeNode[]): TreeNode => ({ children });

describe("isFamilyMonophyletic", () => {
  it("returns true for a single leaf when the family is just that leaf", () => {
    expect(isFamilyMonophyletic(leaf("A"), ["A"])).toBe(true);
  });

  it("returns false for a single leaf when the family contains a different name", () => {
    expect(isFamilyMonophyletic(leaf("A"), ["B"])).toBe(false);
  });

  it("returns true when family members form a complete subtree", () => {
    // ((A,B),(C,D)) — family = {A,B} -> the left subtree is exactly {A,B}.
    const tree = node(node(leaf("A"), leaf("B")), node(leaf("C"), leaf("D")));
    expect(isFamilyMonophyletic(tree, ["A", "B"])).toBe(true);
  });

  it("returns true when family is the WHOLE tree", () => {
    const tree = node(node(leaf("A"), leaf("B")), node(leaf("C"), leaf("D")));
    expect(isFamilyMonophyletic(tree, ["A", "B", "C", "D"])).toBe(true);
  });

  it("returns false when family spans separate clades", () => {
    // ((A,B),(C,D)) — family = {A,D} -> A is in the left clade, D in the
    // right; no subtree contains exactly {A,D}.
    const tree = node(node(leaf("A"), leaf("B")), node(leaf("C"), leaf("D")));
    expect(isFamilyMonophyletic(tree, ["A", "D"])).toBe(false);
  });

  it("returns false when family is a STRICT subset of a clade (clade has extras)", () => {
    // ((A,B,C),(D,E)) — family = {A,B}. The left subtree has 3 leaves
    // including C; no subtree has exactly {A,B}.
    const tree = node(
      node(leaf("A"), leaf("B"), leaf("C")),
      node(leaf("D"), leaf("E")),
    );
    expect(isFamilyMonophyletic(tree, ["A", "B"])).toBe(false);
  });

  it("returns false on an empty family (degenerate)", () => {
    const tree = node(leaf("A"), leaf("B"));
    expect(isFamilyMonophyletic(tree, [])).toBe(false);
  });

  it("deduplicates the family-members list", () => {
    const tree = node(node(leaf("A"), leaf("B")), leaf("C"));
    expect(isFamilyMonophyletic(tree, ["A", "B", "B", "A"])).toBe(true);
  });

  it("returns false when a family member is not in the tree", () => {
    // Family = {A, X}; X is absent — no subtree can contain exactly {A, X}
    // because X isn't a leaf anywhere.
    const tree = node(leaf("A"), leaf("B"));
    expect(isFamilyMonophyletic(tree, ["A", "X"])).toBe(false);
  });

  it("is case-sensitive", () => {
    const tree = node(leaf("AT5G16970"), leaf("AT5G16971"));
    expect(isFamilyMonophyletic(tree, ["at5g16970", "at5g16971"])).toBe(
      false,
    );
    expect(isFamilyMonophyletic(tree, ["AT5G16970", "AT5G16971"])).toBe(true);
  });

  it("handles a deep nesting where the family clade is internal", () => {
    // (((A,B),(C,D)),(E,F)) — family = {C,D}: the (C,D) clade is internal,
    // 2 leaves below; subtree has exactly {C,D}.
    const tree = node(
      node(node(leaf("A"), leaf("B")), node(leaf("C"), leaf("D"))),
      node(leaf("E"), leaf("F")),
    );
    expect(isFamilyMonophyletic(tree, ["C", "D"])).toBe(true);
  });

  it("returns false when the family is split across two non-sibling subtrees", () => {
    // (((A,B),(C,D)),(E,F)) — family = {A,E}: A is far-left, E is right;
    // no internal node contains exactly {A,E}.
    const tree = node(
      node(node(leaf("A"), leaf("B")), node(leaf("C"), leaf("D"))),
      node(leaf("E"), leaf("F")),
    );
    expect(isFamilyMonophyletic(tree, ["A", "E"])).toBe(false);
  });
});
