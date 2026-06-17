import { describe, it, expect } from "vitest";
import { neighborJoining } from "./neighborJoining";
import type { TreeNode } from "./types";
import { isLeaf } from "./types";

// ─── helpers ─────────────────────────────────────────────────────────────────

function countLeaves(node: TreeNode): number {
  if (isLeaf(node)) return 1;
  return (node.children ?? []).reduce((a, c) => a + countLeaves(c), 0);
}

function collectLeafNames(node: TreeNode): string[] {
  if (isLeaf(node)) return node.name ? [node.name] : [];
  return (node.children ?? []).flatMap(collectLeafNames);
}

function countInternalNodes(node: TreeNode): number {
  if (isLeaf(node)) return 0;
  return 1 + (node.children ?? []).reduce(
    (a, c) => a + countInternalNodes(c),
    0,
  );
}

/**
 * Symmetric square matrix builder for tests. Pass the upper triangle row-
 * by-row: pairs[i] is the list of distances from i to i+1, i+2, ... n-1.
 */
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

describe("neighborJoining", () => {
  it("returns a single leaf for n=1", () => {
    const tree = neighborJoining([[0]], ["A"]);
    expect(tree.name).toBe("A");
    expect(tree.children).toBeUndefined();
  });

  it("returns a 2-leaf tree with equal limb lengths for n=2", () => {
    const tree = neighborJoining(sym(2, [[10]]), ["A", "B"]);
    expect(tree.children).toHaveLength(2);
    expect(tree.children![0].distance).toBeCloseTo(5, 10);
    expect(tree.children![1].distance).toBeCloseTo(5, 10);
    expect(collectLeafNames(tree).sort()).toEqual(["A", "B"]);
  });

  it("produces a binary tree (each internal node has exactly 2 children) for n=3", () => {
    // Star-ish tree: equal pairwise distances of 2.
    const tree = neighborJoining(sym(3, [[2, 2], [2]]), ["A", "B", "C"]);
    function checkBinary(n: TreeNode): void {
      if (isLeaf(n)) return;
      expect(n.children).toHaveLength(2);
      n.children!.forEach(checkBinary);
    }
    checkBinary(tree);
  });

  it("preserves all input labels at the leaves", () => {
    const labels = ["A", "B", "C", "D", "E"];
    const tree = neighborJoining(
      sym(5, [
        [5, 9, 9, 8],
        [10, 10, 9],
        [8, 7],
        [3],
      ]),
      labels,
    );
    expect(collectLeafNames(tree).sort()).toEqual(labels.sort());
  });

  it("has exactly n-1 internal nodes for n leaves (binary tree property)", () => {
    const labels = ["A", "B", "C", "D", "E"];
    const tree = neighborJoining(
      sym(5, [
        [5, 9, 9, 8],
        [10, 10, 9],
        [8, 7],
        [3],
      ]),
      labels,
    );
    expect(countLeaves(tree)).toBe(5);
    expect(countInternalNodes(tree)).toBe(4); // n - 1 = 4
  });

  it("identifies the unambiguous first-iteration cherry (A,B) on the canonical Saitou–Nei (1987) 5-taxon matrix", () => {
    // Distance matrix from the original NJ paper. The first iteration's
    // Q-matrix has a unique minimum at pair (A, B) (Q = -50), so any
    // correct NJ implementation will pair A and B as a cherry. Iteration
    // 2 has a tie between Q(u1, C) and Q(D, E) at -28; different
    // implementations break this tie differently and produce
    // mathematically equivalent topologies. So we assert the unambiguous
    // (A, B) cherry only.
    const labels = ["A", "B", "C", "D", "E"];
    const D = sym(5, [
      [5, 9, 9, 8],
      [10, 10, 9],
      [8, 7],
      [3],
    ]);
    const tree = neighborJoining(D, labels);

    function findCherries(n: TreeNode, out: Set<string>): void {
      if (isLeaf(n)) return;
      const kids = n.children ?? [];
      if (kids.length === 2 && isLeaf(kids[0]) && isLeaf(kids[1])) {
        const pair = [kids[0].name, kids[1].name].sort().join("-");
        out.add(pair);
      }
      kids.forEach((k) => findCherries(k, out));
    }
    const cherries = new Set<string>();
    findCherries(tree, cherries);
    expect(cherries.has("A-B")).toBe(true);
  });

  it("produces non-negative branch lengths", () => {
    const labels = ["A", "B", "C", "D"];
    const tree = neighborJoining(
      sym(4, [
        [7, 11, 14],
        [6, 9],
        [7],
      ]),
      labels,
    );
    function checkDistances(n: TreeNode): void {
      if (n.distance !== undefined) {
        expect(n.distance).toBeGreaterThanOrEqual(0);
      }
      (n.children ?? []).forEach(checkDistances);
    }
    checkDistances(tree);
  });

  it("does not mutate the input distance matrix or labels", () => {
    const D = sym(4, [
      [5, 9, 9],
      [10, 10],
      [8],
    ]);
    const labels = ["A", "B", "C", "D"];
    const DCopy = D.map((row) => [...row]);
    const labelsCopy = [...labels];
    neighborJoining(D, labels);
    expect(D).toEqual(DCopy);
    expect(labels).toEqual(labelsCopy);
  });

  it("handles a deterministically-seeded larger input without crashing", () => {
    // 12 labels with a fixed pseudo-random symmetric matrix (deterministic
    // seed via index arithmetic). The test asserts shape, not exact topology
    // (which is sensitive to floating-point drift on larger inputs).
    const n = 12;
    const labels = Array.from({ length: n }, (_, i) => `G${i}`);
    const D: number[][] = Array.from({ length: n }, () =>
      new Array(n).fill(0),
    );
    for (let i = 0; i < n - 1; i += 1) {
      for (let j = i + 1; j < n; j += 1) {
        // Stable pseudo-random in (0.1, 5.0] from indices.
        const d = ((i * 31 + j * 17 + 7) % 49) / 10 + 0.1;
        D[i][j] = d;
        D[j][i] = d;
      }
    }
    const tree = neighborJoining(D, labels);
    expect(countLeaves(tree)).toBe(n);
    expect(countInternalNodes(tree)).toBe(n - 1);
    expect(collectLeafNames(tree).sort()).toEqual(labels.sort());
  });

  it("throws on empty input", () => {
    expect(() => neighborJoining([], [])).toThrow(/zero labels/);
  });

  it("throws on non-square matrix", () => {
    expect(() =>
      neighborJoining(
        [
          [0, 1, 2],
          [1, 0], // wrong length
          [2, 1, 0],
        ],
        ["A", "B", "C"],
      ),
    ).toThrow(/not square/);
  });

  it("throws on row-count vs label-count mismatch", () => {
    expect(() =>
      neighborJoining(sym(3, [[1, 1], [1]]), ["A", "B"]),
    ).toThrow(/rows but labels/);
  });

  it("throws on NaN distances", () => {
    const D = sym(3, [[1, 1], [1]]);
    D[0][1] = NaN;
    D[1][0] = NaN;
    expect(() => neighborJoining(D, ["A", "B", "C"])).toThrow(/NaN/);
  });

  it("treats a fully-zero matrix as a degenerate-but-valid input (no crash)", () => {
    const labels = ["A", "B", "C"];
    const D = sym(3, [[0, 0], [0]]);
    const tree = neighborJoining(D, labels);
    expect(collectLeafNames(tree).sort()).toEqual(labels.sort());
  });

  it("throws on asymmetric off-diagonal entries", () => {
    const D = sym(3, [[1, 1], [1]]);
    D[0][2] = 99; // break symmetry: D[0][2] ≠ D[2][0]
    expect(() => neighborJoining(D, ["A", "B", "C"])).toThrow(/asymmetric/);
  });

  it("throws on non-zero diagonal", () => {
    const D = sym(3, [[1, 1], [1]]);
    D[1][1] = 0.5; // self-distance must be 0
    expect(() => neighborJoining(D, ["A", "B", "C"])).toThrow(/non-zero diagonal/);
  });
});
