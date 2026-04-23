/**
 * GLSL shader sources for the Expression UMAP canvas.
 *
 * Two rendering modes:
 *   - Cluster-colored: each cell's color is a vec4 from the cluster palette,
 *     passed as a per-vertex attribute.
 *   - Gene-expression-colored: each cell's log-normalized expression value is
 *     passed as a per-vertex float attribute; the fragment shader maps it
 *     through the viridis LUT (uniform array) after min/max normalization.
 *
 * The vertex shader is shared between modes (positions, zoom, translate,
 * pointSize, visibility). The mode is selected at the regl draw-command
 * level by choosing which fragment shader to pair with.
 */

/** Shared point-scatter vertex shader — used by both rendering modes. */
export const POINT_VERT = `
  precision mediump float;
  attribute vec2 position;
  attribute vec4 color;
  attribute float visible;
  uniform float zoom;
  uniform vec2 translate;
  uniform float pointSize;
  varying vec4 fragColor;
  varying float v_visible;
  void main() {
    vec2 p = (position + translate) * zoom;
    gl_Position = vec4(p, 0.0, 1.0);
    gl_PointSize = pointSize;
    fragColor = color;
    v_visible = visible;
  }
`;

/** Fragment shader — cluster-colored mode. */
export const CLUSTER_FRAG = `
  precision mediump float;
  varying vec4 fragColor;
  varying float v_visible;
  void main() {
    if (v_visible < 0.5) discard;
    vec2 d = gl_PointCoord - vec2(0.5);
    float r = length(d);
    if (r > 0.5) discard;
    float edge = smoothstep(0.5, 0.42, r);
    gl_FragColor = vec4(fragColor.rgb, fragColor.a * edge);
  }
`;

/**
 * Gene-expression vertex shader — takes a per-cell float expression attribute
 * instead of a vec4 color. Normalizes against uniforms expMin and expMax.
 */
export const EXPRESSION_VERT = `
  precision mediump float;
  attribute vec2 position;
  attribute float expression;
  attribute float visible;
  uniform float zoom;
  uniform vec2 translate;
  uniform float pointSize;
  uniform float expMin;
  uniform float expMax;
  varying float t;
  varying float v_visible;
  void main() {
    vec2 p = (position + translate) * zoom;
    gl_Position = vec4(p, 0.0, 1.0);
    gl_PointSize = pointSize;
    float denom = max(expMax - expMin, 1e-6);
    t = clamp((expression - expMin) / denom, 0.0, 1.0);
    v_visible = visible;
  }
`;

/**
 * Fragment shader — gene-expression mode. Samples the 11-stop viridis LUT
 * using the normalized expression value t.
 *
 * WebGL 1 / GLSL ES 1.0 forbids dynamic indexing into uniform arrays in the
 * fragment shader (must be a constant expression). We work around this by
 * baking the 11 stops in as `const vec3` and using an unrolled if/else
 * ladder. The stop values MUST match VIRIDIS_STOPS in viridis.ts.
 */
export const EXPRESSION_FRAG = `
  precision mediump float;
  varying float t;
  varying float v_visible;

  const vec3 V0  = vec3(0.267, 0.005, 0.329);
  const vec3 V1  = vec3(0.283, 0.141, 0.458);
  const vec3 V2  = vec3(0.254, 0.265, 0.530);
  const vec3 V3  = vec3(0.207, 0.372, 0.553);
  const vec3 V4  = vec3(0.164, 0.471, 0.558);
  const vec3 V5  = vec3(0.128, 0.567, 0.551);
  const vec3 V6  = vec3(0.135, 0.659, 0.518);
  const vec3 V7  = vec3(0.267, 0.749, 0.441);
  const vec3 V8  = vec3(0.478, 0.821, 0.318);
  const vec3 V9  = vec3(0.741, 0.873, 0.150);
  const vec3 V10 = vec3(0.993, 0.906, 0.144);

  vec3 viridisLookup(float tClamp) {
    float scaled = tClamp * 10.0;
    float f = fract(scaled);
    if (scaled < 1.0) return mix(V0, V1, f);
    if (scaled < 2.0) return mix(V1, V2, f);
    if (scaled < 3.0) return mix(V2, V3, f);
    if (scaled < 4.0) return mix(V3, V4, f);
    if (scaled < 5.0) return mix(V4, V5, f);
    if (scaled < 6.0) return mix(V5, V6, f);
    if (scaled < 7.0) return mix(V6, V7, f);
    if (scaled < 8.0) return mix(V7, V8, f);
    if (scaled < 9.0) return mix(V8, V9, f);
    return mix(V9, V10, f);
  }

  void main() {
    if (v_visible < 0.5) discard;
    vec2 d = gl_PointCoord - vec2(0.5);
    float r = length(d);
    if (r > 0.5) discard;
    float edge = smoothstep(0.5, 0.42, r);
    vec3 col = viridisLookup(clamp(t, 0.0, 1.0));
    gl_FragColor = vec4(col, edge);
  }
`;
