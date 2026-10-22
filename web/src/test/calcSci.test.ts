// 2026-10-23 (review fixes): scientific notation, a missing operator between numbers, signs
// after √ / functions / ^, and real powers of negatives in the calculator.
import { describe, expect, it } from "vitest";
import { evaluateExpr } from "../components/student/Calculator";
import { CalcError, calculate } from "../lib/calcExpr";

const calc = (s: string, env = {}) => calculate(s, env);
const err = (s: string) => {
  try {
    calc(s);
  } catch (e) {
    expect(e).toBeInstanceOf(CalcError);
    return (e as Error).message;
  }
  throw new Error(`no error for ${s}`);
};

describe("calculator: scientific notation", () => {
  it("reads 1e5, 6.02e23, 1.e3, 2.5E-3 and .5e2 as numbers", () => {
    expect(calc("1e5")).toBe(1e5);
    expect(calc("6.02e23")).toBe(6.02e23);
    expect(calc("1.e3")).toBe(1000);
    expect(calc("2.5E-3")).toBe(0.0025);
    expect(calc(".5e2")).toBe(50);
    expect(calc("2e+1")).toBe(20);
    expect(calc("1e3*2")).toBe(2000);
    expect(calc("3e2^2")).toBe(90000);
  });
  it("keeps e as the constant when no digits follow", () => {
    expect(calc("2e")).toBeCloseTo(2 * Math.E, 12);
    expect(calc("2e^2")).toBeCloseTo(2 * Math.E ** 2, 12);
    expect(calc("e^2")).toBeCloseTo(Math.E ** 2, 12);
    expect(calc("2exp(1)")).toBeCloseTo(2 * Math.E, 12);
    expect(calc("2e-1")).toBeCloseTo(0.2, 12); // sign + digits: scientific
    expect(calc("2e - 1")).toBeCloseTo(2 * Math.E - 1, 12);
  });
  it("rejects broken numbers", () => {
    expect(err("1e5.5")).toMatch(/number/);
    expect(err(".e5")).toMatch(/number/);
  });
});

describe("calculator: two numbers in a row", () => {
  it("asks for the operator instead of multiplying", () => {
    expect(err("2 3")).toBe("Missing an operator");
    expect(err("1.5 2")).toBe("Missing an operator");
    expect(err("1e3 4")).toBe("Missing an operator");
    expect(calc("2(3)")).toBe(6); // brackets still multiply
    expect(calc("2π")).toBeCloseTo(2 * Math.PI, 12);
  });
});

describe("calculator: signs after √, functions and ^", () => {
  it("takes a sign", () => {
    expect(calc("2^-1")).toBe(0.5);
    expect(calc("sin -30", { angle: "deg" })).toBeCloseTo(-0.5, 12);
    expect(calc("√-4")).toBeNaN();
    expect(calc("√--4")).toBe(2);
    expect(calc("abs -3 + 1")).toBe(4);
    expect(calc("√4 + 1")).toBe(3);
  });
  it("gives real powers of negatives with an odd denominator", () => {
    expect(calc("(-8)^(2/3)")).toBeCloseTo(4, 12);
    expect(calc("(-8)^(1/3)")).toBeCloseTo(-2, 12);
    expect(calc("(-8)^(-1/3)")).toBeCloseTo(-0.5, 12);
    expect(calc("(-32)^(0.6)")).toBeCloseTo(-8, 9); // 3/5: (-2)^3
    expect(calc("(-8)^(1/2)")).toBeNaN();
    expect(calc("(-2)^π")).toBeNaN();
  });
  it("shows no real answer as an error, not as NaN", () => {
    expect(evaluateExpr("√-4", 0, "rad")).toEqual({ ok: false, error: "Not a real number" });
    expect(evaluateExpr("(-8)^(1/2)", 0, "rad")).toEqual({ ok: false, error: "Not a real number" });
    expect(evaluateExpr("(-8)^(2/3)", 0, "rad")).toMatchObject({ ok: true, text: "4" });
    expect(evaluateExpr("2 3", 0, "rad")).toEqual({ ok: false, error: "Missing an operator" });
  });
});
