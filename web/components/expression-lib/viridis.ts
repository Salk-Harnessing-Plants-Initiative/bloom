/**
 * Viridis colormap LUT (11 stops) ported from the Expression Explorer
 * prototype's `data.jsx`. Used by both the JS viridis() helper and the
 * WebGL fragment shader (uniform vec3 viridis[11]).
 *
 * Each stop is [r, g, b] with components in [0, 1].
 */

export const VIRIDIS_STOPS: readonly (readonly [number, number, number])[] = [
  [0.267, 0.005, 0.329],
  [0.283, 0.141, 0.458],
  [0.254, 0.265, 0.530],
  [0.207, 0.372, 0.553],
  [0.164, 0.471, 0.558],
  [0.128, 0.567, 0.551],
  [0.135, 0.659, 0.518],
  [0.267, 0.749, 0.441],
  [0.478, 0.821, 0.318],
  [0.741, 0.873, 0.150],
  [0.993, 0.906, 0.144],
] as const;

/**
 * Map t in [0, 1] to an [r, g, b] triple via linear interpolation between
 * the VIRIDIS_STOPS. Values outside [0, 1] are clamped.
 */
export function viridis(t: number): [number, number, number] {
  const clamped = Math.max(0, Math.min(1, t));
  const n = VIRIDIS_STOPS.length - 1;
  const idx = Math.min(n - 1, Math.floor(clamped * n));
  const f = clamped * n - idx;
  const a = VIRIDIS_STOPS[idx];
  const b = VIRIDIS_STOPS[idx + 1];
  return [
    a[0] + (b[0] - a[0]) * f,
    a[1] + (b[1] - a[1]) * f,
    a[2] + (b[2] - a[2]) * f,
  ];
}

/**
 * Flatten VIRIDIS_STOPS into a Float32Array of length 33 for passing
 * to WebGL as a `uniform vec3 viridis[11]` block.
 */
export function viridisUniformArray(): Float32Array {
  const out = new Float32Array(VIRIDIS_STOPS.length * 3);
  for (let i = 0; i < VIRIDIS_STOPS.length; i++) {
    out[i * 3] = VIRIDIS_STOPS[i][0];
    out[i * 3 + 1] = VIRIDIS_STOPS[i][1];
    out[i * 3 + 2] = VIRIDIS_STOPS[i][2];
  }
  return out;
}
