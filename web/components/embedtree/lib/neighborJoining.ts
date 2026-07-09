/**
 * Builds a binary tree from a pairwise distance matrix.
 *
 * Input:  an N x N symmetric distance matrix + N leaf labels.
 * Output: a rooted binary tree (every internal node has 2 children).
 *
 * The classic NJ algorithm doesn't pick a root; we use the last merge
 * as the root by convention. Which one is the root only affects how
 * the tree is drawn — the actual relationships between leaves are
 * the same either way.
 *
 * Complexity: O(n³) time, O(n²) memory. Fine for embedtree's expected
 * input sizes (n ≤ a few hundred from KNN results); larger inputs
 * would want FastNJ or FastME instead.
 *
 * This function does not modify the input matrix or labels.
 *
 * Throws on: non-square matrix, label/row count mismatch, empty input,
 * NaN distances, non-zero diagonal, or asymmetric off-diagonal entries.
 */
import type { TreeNode } from "./types";

export function neighborJoining(
  distanceMatrix: number[][],
  labels: string[],
): TreeNode {
  const n = labels.length;

  if (n === 0) {
    throw new Error("neighborJoining: cannot build a tree from zero labels");
  }
  if (distanceMatrix.length !== n) {
    throw new Error(
      `neighborJoining: matrix has ${distanceMatrix.length} rows but labels has ${n}`,
    );
  }
  for (let i = 0; i < n; i += 1) {
    if (distanceMatrix[i].length !== n) {
      throw new Error(
        `neighborJoining: matrix is not square at row ${i} (length ${distanceMatrix[i].length}, expected ${n})`,
      );
    }
    for (let j = 0; j < n; j += 1) {
      if (Number.isNaN(distanceMatrix[i][j])) {
        throw new Error(
          `neighborJoining: NaN distance at (${i}, ${j}) — refusing to build a tree with undefined edge lengths`,
        );
      }
    }
    if (distanceMatrix[i][i] !== 0) {
      throw new Error(
        `neighborJoining: non-zero diagonal at (${i}, ${i}) = ${distanceMatrix[i][i]} — distance from a point to itself must be 0`,
      );
    }
    for (let j = i + 1; j < n; j += 1) {
      if (distanceMatrix[i][j] !== distanceMatrix[j][i]) {
        throw new Error(
          `neighborJoining: asymmetric matrix at (${i}, ${j}): D[${i}][${j}] = ${distanceMatrix[i][j]} ≠ D[${j}][${i}] = ${distanceMatrix[j][i]}`,
        );
      }
    }
  }

  if (n === 1) {
    return { name: labels[0] };
  }
  if (n === 2) {
    const d = distanceMatrix[0][1] / 2;
    return {
      children: [
        { name: labels[0], distance: d },
        { name: labels[1], distance: d },
      ],
    };
  }

  // Working copies so we never mutate caller's data. `nodes` slots are
  // nulled out as pairs merge, so the slice itself stays the same length
  // throughout (we only iterate over the still-non-null entries).
  const nodes: (TreeNode | null)[] = labels.map((name) => ({ name }));
  const dist: number[][] = distanceMatrix.map((row) => [...row]);
  let active = nodes.length;

  while (active > 2) {
    // Q-matrix (NJ's selection criterion): pick the pair minimizing
    // (active - 2) * d[i][j] - sum_i - sum_j, where sum_k = Σ d[k][·].
    const sums: number[] = new Array(dist.length).fill(0);
    for (let i = 0; i < dist.length; i += 1) {
      if (!nodes[i]) continue;
      for (let j = 0; j < dist.length; j += 1) {
        if (!nodes[j] || i === j) continue;
        sums[i] += dist[i][j];
      }
    }

    let bestI = -1;
    let bestJ = -1;
    let bestQ = Infinity;
    for (let i = 0; i < dist.length; i += 1) {
      if (!nodes[i]) continue;
      for (let j = i + 1; j < dist.length; j += 1) {
        if (!nodes[j]) continue;
        const q = (active - 2) * dist[i][j] - sums[i] - sums[j];
        if (q < bestQ) {
          bestQ = q;
          bestI = i;
          bestJ = j;
        }
      }
    }

    // Branch lengths from i, j to the new internal node u.
    // Standard NJ formula; clamped to >= 0 because tiny numerical drift
    // can produce ~-1e-15 which makes tree renderers and Newick parsers
    // unhappy.
    const dij = dist[bestI][bestJ];
    const limbI = Math.max(
      0,
      0.5 * dij + (sums[bestI] - sums[bestJ]) / (2 * (active - 2)),
    );
    const limbJ = Math.max(0, dij - limbI);

    const newNode: TreeNode = {
      children: [
        { ...nodes[bestI]!, distance: limbI },
        { ...nodes[bestJ]!, distance: limbJ },
      ],
    };

    // Compute distances from new node u to each remaining other node k:
    //   d[u][k] = (d[i][k] + d[j][k] - d[i][j]) / 2
    const newRow = dist[bestI].slice();
    for (let k = 0; k < dist.length; k += 1) {
      if (!nodes[k] || k === bestI || k === bestJ) {
        newRow[k] = 0;
        continue;
      }
      newRow[k] = Math.max(
        0,
        (dist[bestI][k] + dist[bestJ][k] - dij) / 2,
      );
    }

    // Replace row/col bestI with the merged node; clear bestJ.
    dist[bestI] = newRow;
    for (let k = 0; k < dist.length; k += 1) {
      if (k !== bestI && nodes[k]) {
        dist[k][bestI] = newRow[k];
      }
    }
    nodes[bestJ] = null;
    nodes[bestI] = newNode;
    active -= 1;
  }

  // Two active nodes left: root them under a common parent.
  const remaining: TreeNode[] = [];
  let lastI = -1;
  let lastJ = -1;
  for (let k = 0; k < nodes.length; k += 1) {
    const node = nodes[k];
    if (node) {
      remaining.push(node);
      if (lastI === -1) lastI = k;
      else lastJ = k;
    }
  }
  const half = dist[lastI][lastJ] / 2;
  return {
    children: [
      { ...remaining[0], distance: half },
      { ...remaining[1], distance: half },
    ],
  };
}
