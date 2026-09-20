/**
 * Which cells each expression pass draws. The cells at exactly zero go down
 * first and the expressing cells over them, so an expressing cell is never
 * hidden under one that does not express the gene. The split is on the raw
 * value, never on the colour, so narrowing the colour range cannot move a cell
 * from one pass to the other.
 */

import { describe, expect, it } from "vitest";

import { EXPRESSION_VERT, VALUE_PASS, expressionPasses, inValuePass } from "./shaders";

describe("inValuePass", () => {
  it("puts a cell at zero in the zero pass only", () => {
    expect(inValuePass(0, VALUE_PASS.ZERO)).toBe(true);
    expect(inValuePass(0, VALUE_PASS.POSITIVE)).toBe(false);
  });

  it("puts a cell above zero in the expressing pass only", () => {
    expect(inValuePass(0.001, VALUE_PASS.POSITIVE)).toBe(true);
    expect(inValuePass(0.001, VALUE_PASS.ZERO)).toBe(false);
  });

  it("puts every cell in the one pass that draws them all", () => {
    expect(inValuePass(0, VALUE_PASS.ALL)).toBe(true);
    expect(inValuePass(3, VALUE_PASS.ALL)).toBe(true);
  });

  it("splits on the value, whatever order the cells are stored in and wherever the colour range starts", () => {
    // Stored with the zeros last, and a colour range starting at 1 would paint
    // 0.5 in the same lowest colour as 0: it is still an expressing cell.
    const values = [4, 0.5, 2, 0, 0];
    const zeros = values.filter((v) => inValuePass(v, VALUE_PASS.ZERO));
    const expressing = values.filter((v) => inValuePass(v, VALUE_PASS.POSITIVE));
    expect(zeros).toEqual([0, 0]);
    expect(expressing).toEqual([4, 0.5, 2]);
  });
});

describe("expressionPasses", () => {
  it("draws the zeros, then the expressing cells, when nothing is focused", () => {
    expect(expressionPasses(false)).toEqual([
      { focusMode: 0, valuePass: VALUE_PASS.ZERO },
      { focusMode: 0, valuePass: VALUE_PASS.POSITIVE },
    ]);
  });

  it("draws the greyed-out cells whole, then the focused zeros, then the focused expressing cells", () => {
    expect(expressionPasses(true)).toEqual([
      { focusMode: 1, valuePass: VALUE_PASS.ALL },
      { focusMode: 2, valuePass: VALUE_PASS.ZERO },
      { focusMode: 2, valuePass: VALUE_PASS.POSITIVE },
    ]);
  });
});

/** The shader's own inValuePass, run as JS: its body is plain arithmetic and ternaries. */
function shaderInValuePass(): (expression: number, valuePass: number) => number {
  const found = EXPRESSION_VERT.match(
    /float inValuePass\(float expression, float valuePass\) \{([\s\S]*?)\n\s*\}/,
  );
  if (!found) throw new Error("the expression shader has no inValuePass");
  return new Function("expression", "valuePass", found[1].replace(/\bfloat\s+/g, "")) as (
    expression: number,
    valuePass: number,
  ) => number;
}

describe("the expression shader", () => {
  it("takes the pass as a uniform", () => {
    expect(EXPRESSION_VERT).toContain("uniform float valuePass;");
  });

  it("applies the same rule as inValuePass, for every pass and awkward values", () => {
    const rule = shaderInValuePass();
    for (const pass of Object.values(VALUE_PASS)) {
      for (const value of [-1, 0, 1e-6, 0.5, 3, Number.NaN]) {
        expect(rule(value, pass) === 1, `value ${value}, pass ${pass}`).toBe(inValuePass(value, pass));
      }
    }
  });

  it("keeps a cell only when it is in both the focus pass and the value pass", () => {
    expect(EXPRESSION_VERT).toMatch(
      /v_inPass = inFocusPass\(focus, focusMode\) \* inValuePass\(expression, valuePass\);/,
    );
  });
});
