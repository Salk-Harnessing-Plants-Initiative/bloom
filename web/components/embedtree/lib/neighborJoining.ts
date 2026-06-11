/**
 * Saitou & Nei (1987) Neighbor-Joining tree construction.
 *
 * Input: a square N x N pairwise distance matrix and N leaf labels.
 * Output: a rooted binary TreeNode where each leaf corresponds to one
 * of the input labels and each internal node has exactly two children.
 *
 * The classic NJ algorithm produces an unrooted tree; we mid-point-root
 * implicitly by treating the final 3-leaf join as the root (the standard
 * "neighbor joining tree" rooted at the last internal node). The exact
 * choice of root affects rendering but not topology.
 *
 * Pure: never mutates the input matrix or labels.
 *
 * @throws Error on a non-square matrix, length mismatch with labels,
 *   empty input, or NaN distances.
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

  // Working copies so we never mutate caller's data.
  const nodes: TreeNode[] = labels.map((name) => ({ name }));
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
        { ...nodes[bestI], distance: limbI },
        { ...nodes[bestJ], distance: limbJ },
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
    // @ts-expect-error — we intentionally null out the merged slot
    nodes[bestJ] = undefined;
    nodes[bestI] = newNode;
    active -= 1;
  }

  // Two active nodes left: root them under a common parent.
  const remaining: TreeNode[] = [];
  let lastI = -1;
  let lastJ = -1;
  for (let k = 0; k < nodes.length; k += 1) {
    if (nodes[k]) {
      remaining.push(nodes[k]);
      if (lastI === -1) lastI = k;
      else lastJ = k;
    }
  }
  const finalDist = lastJ >= 0 ? dist[lastI][lastJ] : 0;
  const half = finalDist / 2;
  return {
    children: [
      { ...remaining[0], distance: half },
      { ...remaining[1], distance: half },
    ],
  };
}
