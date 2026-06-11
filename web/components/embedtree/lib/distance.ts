/**
 * Vector distance metrics for embedtree.
 *
 * Both functions are pure: they read their inputs and return a number.
 * Inputs are never mutated.
 *
 * Cosine distance matches what `knn_search_esm2` uses on the database
 * side (pgvector's `<=>` operator), so a tree built from cosineDistance
 * here is comparable with the RPC's similarity ranking.
 */

/**
 * Cosine distance between two equal-length numeric vectors.
 *
 * Returns 1 - cosine_similarity, so:
 *   0   identical direction (cos = 1)
 *   1   orthogonal           (cos = 0)
 *   2   exactly opposite     (cos = -1)
 *
 * A zero vector has undefined direction; we treat
 * cosineDistance(zero, anything) as 1 (maximally dissimilar) rather
 * than NaN so downstream tree construction does not produce NaN-valued
 * matrices that silently corrupt neighbour-joining output.
 *
 * @throws Error if the vectors differ in length.
 */
export function cosineDistance(a: number[], b: number[]): number {
  if (a.length !== b.length) {
    throw new Error(
      `cosineDistance: vector length mismatch (${a.length} vs ${b.length})`,
    );
  }
  if (a.length === 0) {
    throw new Error("cosineDistance: vectors must be non-empty");
  }

  let dot = 0;
  let normA = 0;
  let normB = 0;
  for (let i = 0; i < a.length; i += 1) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }

  if (normA === 0 || normB === 0) {
    return 1;
  }

  const sim = dot / (Math.sqrt(normA) * Math.sqrt(normB));
  // Clamp for floating-point drift outside [-1, 1] so distance stays in [0, 2].
  const clamped = Math.max(-1, Math.min(1, sim));
  return 1 - clamped;
}

/**
 * Euclidean (L2) distance between two equal-length numeric vectors.
 *
 * Returns sqrt(Σ (a_i - b_i)^2). Always >= 0; zero iff identical.
 *
 * @throws Error if the vectors differ in length or are empty.
 */
export function euclideanDistance(a: number[], b: number[]): number {
  if (a.length !== b.length) {
    throw new Error(
      `euclideanDistance: vector length mismatch (${a.length} vs ${b.length})`,
    );
  }
  if (a.length === 0) {
    throw new Error("euclideanDistance: vectors must be non-empty");
  }

  let sum = 0;
  for (let i = 0; i < a.length; i += 1) {
    const d = a[i] - b[i];
    sum += d * d;
  }
  return Math.sqrt(sum);
}
