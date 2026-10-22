// 2026-10-23 (exam tools): the calculator's own parser (no eval).
import { describe, expect, it } from "vitest";
import { CalcError, calculate, compile, formatResult, parse, tokenize } from "../lib/calcExpr";

const calc = (s: string, env = {}) => calculate(s, env);
const close = (a: number, b: number) => expect(Math.abs(a - b)).toBeLessThan(1e-9);

describe("calculator expressions", () => {
  it("follows the usual order of operations", () => {
    expect(calc("2+3*4")).toBe(14);
    expect(calc("(2+3)*4")).toBe(20);
    expect(calc("10-4-3")).toBe(3);
    expect(calc("100/10/5")).toBe(2);
    expect(calc("2^3^2")).toBe(512);          // right-associative
    expect(calc("-2^2")).toBe(-4);            // the power first
    expect(calc("(-2)^2")).toBe(4);
    expect(calc("2^-1")).toBe(0.5);
    expect(calc("--3")).toBe(3);
    expect(calc("+5")).toBe(5);
  });

  it("reads the keypad symbols", () => {
    expect(calc("6×7")).toBe(42);
    expect(calc("9÷3")).toBe(3);
    expect(calc("9−3")).toBe(6);
    expect(calc("2**10")).toBe(1024);
    expect(calc("√16")).toBe(4);
    expect(calc("√(9)+1")).toBe(4);
    close(calc("π"), Math.PI);
    close(calc("e"), Math.E);
  });

  it("multiplies without a sign where a hand calculator does", () => {
    close(calc("2π"), 2 * Math.PI);
    expect(calc("3(4)")).toBe(12);
    expect(calc("(1+1)(2+2)")).toBe(8);
    expect(calc("2√9")).toBe(6);
    close(calc("2x", { x: 1.5 }), 3);
    close(calc("x sin(x)", { x: Math.PI / 2 }), Math.PI / 2);
  });

  it("has degree and radian trig with exact zeros", () => {
    close(calc("sin(30)", { angle: "deg" }), 0.5);
    expect(calc("sin(180)", { angle: "deg" })).toBe(0);
    expect(calc("cos(90)", { angle: "deg" })).toBe(0);
    expect(calc("tan(45)", { angle: "deg" })).toBeCloseTo(1, 12);
    expect(Number.isNaN(calc("tan(90)", { angle: "deg" }))).toBe(true);
    close(calc("sin(π/2)", { angle: "rad" }), 1);
    close(calc("asin(1)", { angle: "deg" }), 90);
    close(calc("acos(0)", { angle: "rad" }), Math.PI / 2);
    close(calc("atan(1)", { angle: "deg" }), 45);
    close(calc("sin 30", { angle: "deg" }), 0.5);       // function without brackets
  });

  it("has logs, roots, abs, exp, factorial and Ans", () => {
    expect(calc("log(1000)")).toBe(3);
    close(calc("ln(e^2)"), 2);
    expect(calc("sqrt(2)^2")).toBeCloseTo(2, 12);
    expect(calc("abs(-3)")).toBe(3);
    close(calc("exp(1)"), Math.E);
    expect(calc("5!")).toBe(120);
    expect(calc("0!")).toBe(1);
    expect(Number.isNaN(calc("2.5!"))).toBe(true);
    expect(calc("3!^2")).toBe(36);
    expect(calc("ans*2", { ans: 21 })).toBe(42);
    expect(calc("(-8)^(1/3)")).toBeCloseTo(-2, 12);      // odd root of a negative
    expect(Number.isNaN(calc("(-8)^(1/2)"))).toBe(true);
  });

  it("closes brackets left open at the end", () => {
    expect(calc("sin(90", { angle: "deg" })).toBe(1);
    expect(calc("2*(3+4")).toBe(14);
  });

  it("explains mistakes instead of guessing", () => {
    const err = (s: string) => {
      try {
        calc(s);
      } catch (e) {
        expect(e).toBeInstanceOf(CalcError);
        return (e as Error).message;
      }
      throw new Error(`no error for ${s}`);
    };
    expect(err("")).toMatch(/Type something/);
    expect(err("2+")).toMatch(/Finish/);
    expect(err("2)")).toMatch(/brackets/);
    expect(err("()")).toMatch(/Empty brackets/);
    expect(err("(2+3))")).toMatch(/brackets/);
    expect(err("1..2")).toMatch(/number/);
    expect(err("foo(2)")).toMatch(/Unknown name "foo/);
    expect(err("x+1")).toMatch(/only for graphs/);
    expect(err("2#3")).toMatch(/Unexpected/);
    expect(err("1".repeat(501))).toMatch(/Too long/);
    expect(err("(".repeat(300) + "1")).toMatch(/Too complex/);
    expect(err("*3")).toMatch(/Missing a number/);
  });

  it("never runs code: names are a fixed list", () => {
    expect(() => tokenize("alert(1)")).toThrow(CalcError);
    expect(() => parse("constructor")).toThrow(CalcError);
    expect(() => parse("this")).toThrow(CalcError);
  });

  it("compiles once for the graph", () => {
    const f = compile("x^2 - 2x + 1", { x: true });
    expect(f({ x: 1 })).toBe(0);
    expect(f({ x: 3 })).toBe(4);
    expect(Number.isFinite(compile("1/x", { x: true })({ x: 0 }))).toBe(false);
  });

  it("formats results without float noise", () => {
    expect(formatResult(0.1 + 0.2)).toBe("0.3");
    expect(formatResult(2 / 3)).toBe("0.666666666667");
    expect(formatResult(-0)).toBe("0");
    expect(formatResult(1e21)).toBe("1×10^21");
    expect(formatResult(1.5e-12)).toBe("1.5×10^-12");
    expect(formatResult(1 / 0)).toBe("Too large");
    expect(formatResult(-1 / 0)).toBe("Too small");
    expect(formatResult(NaN)).toBe("Not a number");
    expect(formatResult(123456789)).toBe("123456789");
  });
});
