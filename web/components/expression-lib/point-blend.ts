/**
 * How the maps blend their points. Colour blends by each point's alpha, but the
 * canvas's own alpha stays 1: blending it the same way thins it wherever faint
 * points pile up, and the page behind the canvas shows through them as white
 * instead of the grey they are drawn in.
 */
export const POINT_BLEND = {
  enable: true,
  func: {
    srcRGB: "src alpha",
    dstRGB: "one minus src alpha",
    srcAlpha: "one",
    dstAlpha: "one minus src alpha",
  },
} as const;
