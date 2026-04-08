import type { DistanceMetric } from "../constants";

export function computeDistanceMatrix(
  vectors: number[][],
  metric: DistanceMetric
): number[][] {
  const n = vectors.length;
  const matrix: number[][] = Array.from({ length: n }, () => new Array(n).fill(0));

  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const d = pairwiseDistance(vectors[i], vectors[j], metric);
      matrix[i][j] = d;
      matrix[j][i] = d;
    }
  }

  return matrix;
}

function pairwiseDistance(a: number[], b: number[], metric: DistanceMetric): number {
  switch (metric) {
    case "euclidean": return euclidean(a, b);
    case "cosine": return cosine(a, b);
    case "correlation": return correlation(a, b);
    case "cityblock": return cityblock(a, b);
  }
}

function euclidean(a: number[], b: number[]): number {
  let sum = 0;
  for (let i = 0; i < a.length; i++) { const d = a[i] - b[i]; sum += d * d; }
  return Math.sqrt(sum);
}

function cosine(a: number[], b: number[]): number {
  let dot = 0, normA = 0, normB = 0;
  for (let i = 0; i < a.length; i++) { dot += a[i] * b[i]; normA += a[i] * a[i]; normB += b[i] * b[i]; }
  const denom = Math.sqrt(normA) * Math.sqrt(normB);
  return denom === 0 ? 1 : 1 - dot / denom;
}

function correlation(a: number[], b: number[]): number {
  const n = a.length;
  let sumA = 0, sumB = 0;
  for (let i = 0; i < n; i++) { sumA += a[i]; sumB += b[i]; }
  const meanA = sumA / n, meanB = sumB / n;
  let cov = 0, varA = 0, varB = 0;
  for (let i = 0; i < n; i++) { const da = a[i] - meanA, db = b[i] - meanB; cov += da * db; varA += da * da; varB += db * db; }
  const denom = Math.sqrt(varA) * Math.sqrt(varB);
  return denom === 0 ? 1 : 1 - cov / denom;
}

function cityblock(a: number[], b: number[]): number {
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += Math.abs(a[i] - b[i]);
  return sum;
}
