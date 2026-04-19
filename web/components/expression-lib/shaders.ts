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
 */
export const EXPRESSION_FRAG = `
  precision mediump float;
  varying float t;
  varying float v_visible;
  uniform vec3 viridis[11];
  void main() {
    if (v_visible < 0.5) discard;
    vec2 d = gl_PointCoord - vec2(0.5);
    float r = length(d);
    if (r > 0.5) discard;
    float edge = smoothstep(0.5, 0.42, r);
    float scaled = t * 10.0;
    int idx = int(floor(scaled));
    if (idx < 0) idx = 0;
    if (idx > 9) idx = 9;
    float f = scaled - float(idx);
    vec3 a = viridis[idx];
    vec3 b = viridis[idx + 1];
    vec3 col = mix(a, b, f);
    gl_FragColor = vec4(col, edge);
  }
`;
