import { describe, it, expect } from "vitest";
import { toNewick, parseNewick } from "./newick";
import { neighborJoining } from "./neighborJoining";
import type { TreeNode } from "./types";
import { isLeaf } from "./types";

// ─── helpers ─────────────────────────────────────────────────────────────────

function leafSet(node: TreeNode): Set<string> {
  if (isLeaf(node)) return new Set(node.name ? [node.name] : []);
  const out = new Set<string>();
  for (const c of node.children ?? []) {
    for (const n of leafSet(c)) out.add(n);
  }
  return out;
}

/**
 * Compare two trees for topological + branch-length equivalence
 * (modulo child ordering). Used to verify round-trip identity.
 */
function topologicallyEqual(a: TreeNode, b: TreeNode): boolean {
  if (isLeaf(a) !== isLeaf(b)) return false;
  if (isLeaf(a)) return (a.name ?? "") === (b.name ?? "");
  const ac = a.children ?? [];
  const bc = b.children ?? [];
  if (ac.length !== bc.length) return false;
  // Match each child of a to a child of b by leaf set.
  const bUnmatched = [...bc];
  for (const child of ac) {
    const childLeaves = [...leafSet(child)].sort().join("|");
    const idx = bUnmatched.findIndex(
      (cand) => [...leafSet(cand)].sort().join("|") === childLeaves,
    );
    if (idx === -1) return false;
    if (!topologicallyEqual(child, bUnmatched[idx])) return false;
    bUnmatched.splice(idx, 1);
  }
  return true;
}

function sym(n: number, pairs: number[][]): number[][] {
  const m: number[][] = Array.from({ length: n }, () =>
    new Array(n).fill(0),
  );
  for (let i = 0; i < n - 1; i += 1) {
    for (let j = i + 1; j < n; j += 1) {
      const d = pairs[i][j - i - 1];
      m[i][j] = d;
      m[j][i] = d;
    }
  }
  return m;
}

// ─── tests ───────────────────────────────────────────────────────────────────

describe("toNewick", () => {
  it("serialises a single leaf as `<name>;`", () => {
    expect(toNewick({ name: "A" })).toBe("A;");
  });

  it("serialises a two-leaf tree with branch lengths", () => {
    const t: TreeNode = {
      children: [
        { name: "A", distance: 0.1 },
        { name: "B", distance: 0.2 },
      ],
    };
    expect(toNewick(t)).toBe("(A:0.1,B:0.2);");
  });

  it("serialises a nested tree", () => {
    const t: TreeNode = {
      children: [
        {
          children: [
            { name: "A", distance: 0.1 },
            { name: "B", distance: 0.2 },
          ],
          distance: 0.05,
        },
        { name: "C", distance: 0.3 },
      ],
    };
    expect(toNewick(t)).toBe("((A:0.1,B:0.2):0.05,C:0.3);");
  });

  it("omits a missing branch length", () => {
    expect(toNewick({ children: [{ name: "A" }, { name: "B" }] })).toBe(
      "(A,B);",
    );
  });

  it("quotes names containing reserved characters", () => {
    expect(toNewick({ name: "weird name (1)" })).toBe("'weird name (1)';");
  });

  it("escapes embedded single quotes by doubling them", () => {
    expect(toNewick({ name: "it's:fine" })).toBe("'it''s:fine';");
  });
});

describe("parseNewick", () => {
  it("parses a single leaf", () => {
    const t = parseNewick("A;");
    expect(isLeaf(t)).toBe(true);
    expect(t.name).toBe("A");
  });

  it("parses a two-leaf tree with branch lengths", () => {
    const t = parseNewick("(A:0.1,B:0.2);");
    expect(t.children).toHaveLength(2);
    expect(t.children![0].name).toBe("A");
    expect(t.children![0].distance).toBeCloseTo(0.1, 12);
    expect(t.children![1].name).toBe("B");
    expect(t.children![1].distance).toBeCloseTo(0.2, 12);
  });

  it("parses a nested tree", () => {
    const t = parseNewick("((A:0.1,B:0.2):0.05,C:0.3);");
    expect(t.children).toHaveLength(2);
    expect(t.children![0].children).toHaveLength(2);
    expect(t.children![0].distance).toBeCloseTo(0.05, 12);
    expect(t.children![1].name).toBe("C");
  });

  it("tolerates the optional trailing semicolon being absent", () => {
    const a = parseNewick("(A,B);");
    const b = parseNewick("(A,B)");
    expect(topologicallyEqual(a, b)).toBe(true);
  });

  it("tolerates surrounding whitespace and internal whitespace", () => {
    const t = parseNewick("  ( A:0.1 , B:0.2 ) ; ");
    expect(t.children![0].name).toBe("A");
    expect(t.children![1].name).toBe("B");
  });

  it("parses quoted names with embedded reserved characters", () => {
    const t = parseNewick("('it''s:fine','weird name (1)');");
    expect(t.children![0].name).toBe("it's:fine");
    expect(t.children![1].name).toBe("weird name (1)");
  });

  it("throws on unbalanced parentheses", () => {
    expect(() => parseNewick("(A,B;")).toThrow();
  });

  it("throws on empty input", () => {
    expect(() => parseNewick("")).toThrow(/empty input/);
  });
});

describe("toNewick ↔ parseNewick round-trip", () => {
  it("round-trips a simple manually-built tree", () => {
    const original: TreeNode = {
      children: [
        {
          children: [
            { name: "A", distance: 0.1 },
            { name: "B", distance: 0.2 },
          ],
          distance: 0.05,
        },
        { name: "C", distance: 0.3 },
      ],
    };
    const roundTripped = parseNewick(toNewick(original));
    expect(topologicallyEqual(original, roundTripped)).toBe(true);
  });

  it("round-trips a tree produced by neighborJoining (5 taxa)", () => {
    const labels = ["A", "B", "C", "D", "E"];
    const D = sym(5, [
      [5, 9, 9, 8],
      [10, 10, 9],
      [8, 7],
      [3],
    ]);
    const tree = neighborJoining(D, labels);
    const roundTripped = parseNewick(toNewick(tree));
    expect(leafSet(roundTripped)).toEqual(new Set(labels));
    expect(topologicallyEqual(tree, roundTripped)).toBe(true);
  });

  it("round-trips a tree produced by neighborJoining (12 taxa, deterministic input)", () => {
    const n = 12;
    const labels = Array.from({ length: n }, (_, i) => `G${i}`);
    const D: number[][] = Array.from({ length: n }, () =>
      new Array(n).fill(0),
    );
    for (let i = 0; i < n - 1; i += 1) {
      for (let j = i + 1; j < n; j += 1) {
        const d = ((i * 31 + j * 17 + 7) % 49) / 10 + 0.1;
        D[i][j] = d;
        D[j][i] = d;
      }
    }
    const tree = neighborJoining(D, labels);
    const roundTripped = parseNewick(toNewick(tree));
    expect(leafSet(roundTripped)).toEqual(new Set(labels));
    expect(topologicallyEqual(tree, roundTripped)).toBe(true);
  });
});
