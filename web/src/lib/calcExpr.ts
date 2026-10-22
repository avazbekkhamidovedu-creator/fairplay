// 2026-10-23 (exam tools): the calculator's expression language. A small tokenizer and a
// recursive-descent parser build a tree that is evaluated directly: no eval / new Function
// (the exam page's CSP forbids them anyway). Shared by the scientific keypad and the graph.
//
//   expr    := sum
//   sum     := product (("+" | "-") product)*
//   product := unary (("*" | "/") unary | <implicit> power)*     2π, 3(4), 2x, x sin(x)
//   unary   := ("-" | "+") unary | power
//   power   := postfix ("^" unary)?                               right-assoc, -2^2 = -4
//   postfix := primary "!"*
//   primary := number | name | func "(" expr ")" | func unary | "√" unary | "(" expr ")"
//
// Unclosed "(" at the very end are closed for the student, like a hand calculator.
// 2026-10-23: numbers take scientific notation (1e5, 6.02e23, 1.e3, 2.5E-3): a number
// directly followed by e/E, an optional sign and digits. Anything else keeps e as the
// constant (2e = 2·e, 2e^2). Two numbers in a row ("2 3") are an error, not 6. √, the
// functions and ^ take a sign (√-4, sin -30, 2^-1).

export type AngleMode = "deg" | "rad";

export class CalcError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CalcError";
  }
}

type Node =
  | { t: "num"; v: number }
  | { t: "var"; n: "x" | "ans" }
  | { t: "neg"; a: Node }
  | { t: "bin"; op: "+" | "-" | "*" | "/" | "^"; a: Node; b: Node }
  | { t: "call"; f: FuncName; a: Node }
  | { t: "fact"; a: Node };

type FuncName = "sin" | "cos" | "tan" | "asin" | "acos" | "atan" | "sqrt" | "log" | "ln" | "abs" | "exp";
const FUNCS: readonly FuncName[] = ["sin", "cos", "tan", "asin", "acos", "atan", "sqrt", "log", "ln", "abs", "exp"];
const CONSTS: Record<string, number> = { pi: Math.PI, "π": Math.PI, e: Math.E };
/** Longest first, so "exp" is not read as e·x·p and "asin" not as a·sin. */
const NAMES = ["asin", "acos", "atan", "sqrt", "ans", "sin", "cos", "tan", "log", "abs", "exp", "ln", "pi", "π", "e", "x"];

type Tok =
  | { k: "num"; v: number }
  | { k: "name"; v: string }
  | { k: "op"; v: "+" | "-" | "*" | "/" | "^" | "(" | ")" | "!" | "√" };

const MAX_INPUT = 500;

export function tokenize(src: string): Tok[] {
  if (src.length > MAX_INPUT) throw new CalcError("Too long");
  const out: Tok[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i] as string;
    if (/\s/.test(c)) {
      i++;
      continue;
    }
    if (/[0-9.]/.test(c)) {
      let j = i;
      while (j < src.length && /[0-9]/.test(src[j] as string)) j++;
      if (src[j] === ".") {
        j++;
        while (j < src.length && /[0-9]/.test(src[j] as string)) j++;
      }
      if (j === i + 1 && c === ".") throw new CalcError("Check the number");
      // scientific notation: e/E, an optional sign, then at least one digit
      const sci = /^[eE][+-]?[0-9]+/.exec(src.slice(j));
      if (sci) j += sci[0].length;
      const text = src.slice(i, j);
      // "1..2", "1.2.3", "1e5.5": not one number
      if (text === "." || src[j] === ".") throw new CalcError("Check the number");
      out.push({ k: "num", v: Number(text) });
      i = j;
      continue;
    }
    if (src.startsWith("**", i)) {
      out.push({ k: "op", v: "^" });
      i += 2;
      continue;
    }
    const op = ({ "+": "+", "-": "-", "−": "-", "–": "-", "*": "*", "×": "*", "·": "*", "/": "/", "÷": "/",
      "^": "^", "(": "(", ")": ")", "!": "!", "√": "√" } as const)[c as "+"];
    if (op) {
      out.push({ k: "op", v: op });
      i++;
      continue;
    }
    if (/[a-zA-Zπ]/.test(c)) {
      const rest = src.slice(i).toLowerCase();
      const name = NAMES.find((n) => rest.startsWith(n));
      if (!name) throw new CalcError(`Unknown name "${src.slice(i).match(/^[a-zA-Zπ]+/)?.[0] ?? c}"`);
      out.push({ k: "name", v: name });
      i += name.length;
      continue;
    }
    throw new CalcError(`Unexpected "${c}"`);
  }
  return out;
}

export interface ParseOptions {
  /** allow the variable x (graphing) */
  x?: boolean;
}

export function parse(src: string, opts: ParseOptions = {}): Node {
  const toks = tokenize(src);
  let p = 0;
  let depth = 0;
  const peek = () => toks[p];
  const isOp = (v: string) => {
    const t = toks[p];
    return !!t && t.k === "op" && t.v === v;
  };
  const guard = () => {
    if (++depth > 200) throw new CalcError("Too complex");
  };

  function sum(): Node {
    let a = product();
    while (isOp("+") || isOp("-")) {
      const op = (toks[p++] as { v: "+" | "-" }).v;
      a = { t: "bin", op, a, b: product() };
    }
    return a;
  }
  function startsOperand(): boolean {
    const t = peek();
    return !!t && (t.k === "num" || t.k === "name" || (t.k === "op" && (t.v === "(" || t.v === "√")));
  }
  function product(): Node {
    let a = unary();
    for (;;) {
      if (isOp("*") || isOp("/")) {
        const op = (toks[p++] as { v: "*" | "/" }).v;
        a = { t: "bin", op, a, b: unary() };
      } else if (startsOperand()) {
        const prev = toks[p - 1];
        if (prev && prev.k === "num" && peek()?.k === "num") throw new CalcError("Missing an operator");
        a = { t: "bin", op: "*", a, b: power() };      // implicit: 2π, 3(4), 2x
      } else return a;
    }
  }
  function unary(): Node {
    guard();
    try {
      if (isOp("-")) {
        p++;
        return { t: "neg", a: unary() };
      }
      if (isOp("+")) {
        p++;
        return unary();
      }
      return power();
    } finally {
      depth--;
    }
  }
  function power(): Node {
    const base = postfix();
    if (isOp("^")) {
      p++;
      if (!peek()) throw new CalcError("Finish the power");
      return { t: "bin", op: "^", a: base, b: unary() };
    }
    return base;
  }
  function postfix(): Node {
    let a = primary();
    while (isOp("!")) {
      p++;
      a = { t: "fact", a };
    }
    return a;
  }
  function group(): Node {
    // after "(": an expression, then ")" (or the end of the input: closed for the student)
    if (isOp(")")) throw new CalcError("Empty brackets");
    const inner = sum();
    if (isOp(")")) p++;
    else if (peek()) throw new CalcError("Check the brackets");
    return inner;
  }
  function primary(): Node {
    guard();
    try {
      const t = toks[p];
      if (!t) throw new CalcError("Finish the expression");
      if (t.k === "num") {
        p++;
        return { t: "num", v: t.v };
      }
      if (t.k === "op" && t.v === "(") {
        p++;
        return group();
      }
      if (t.k === "op" && t.v === "√") {
        p++;
        return { t: "call", f: "sqrt", a: unary() };
      }
      if (t.k === "name") {
        p++;
        if ((FUNCS as readonly string[]).includes(t.v)) {
          const f = t.v as FuncName;
          if (isOp("(")) {
            p++;
            return { t: "call", f, a: group() };
          }
          return { t: "call", f, a: unary() };            // sin x, ln 2, sin -30
        }
        if (t.v === "x") {
          if (!opts.x) throw new CalcError("x is only for graphs");
          return { t: "var", n: "x" };
        }
        if (t.v === "ans") return { t: "var", n: "ans" };
        return { t: "num", v: CONSTS[t.v] as number };
      }
      throw new CalcError(t.v === ")" ? "Check the brackets" : `Missing a number before "${t.v}"`);
    } finally {
      depth--;
    }
  }

  if (toks.length === 0) throw new CalcError("Type something to calculate");
  const tree = sum();
  if (p < toks.length) throw new CalcError(isOp(")") ? "Check the brackets" : "Check the expression");
  return tree;
}

export interface Env {
  x?: number;
  ans?: number;
  angle?: AngleMode;
}

/** b as p/q with q <= 1000 (continued fractions), or null when no such fraction is close. */
function smallFraction(b: number): { p: number; q: number } | null {
  let h0 = 0, h1 = 1, k0 = 1, k1 = 0;
  let x = b;
  for (let i = 0; i < 32; i++) {
    const a = Math.floor(x);
    const h = a * h1 + h0;
    const k = a * k1 + k0;
    if (k > 1000) break;
    h0 = h1; h1 = h; k0 = k1; k1 = k;
    if (Math.abs(h1 / k1 - b) < 1e-9) return { p: Math.abs(h1), q: k1 };
    const frac = x - a;
    if (frac < 1e-12) break;
    x = 1 / frac;
  }
  return null;
}

function factorial(n: number): number {
  if (!Number.isInteger(n) || n < 0) return NaN;
  if (n > 170) return Infinity;
  let r = 1;
  for (let i = 2; i <= n; i++) r *= i;
  return r;
}

/** Trig in degrees with exact zeros (sin 180 = 0, cos 90 = 0, tan 90 = undefined). */
function trig(f: "sin" | "cos" | "tan", v: number, angle: AngleMode): number {
  if (angle === "deg") {
    const d = ((v % 360) + 360) % 360;
    if (f === "sin" && d % 180 === 0) return 0;
    if (f === "cos" && (d === 90 || d === 270)) return 0;
    if (f === "tan" && d % 180 === 0) return 0;
    if (f === "tan" && (d === 90 || d === 270)) return NaN;
    v = (v * Math.PI) / 180;
  }
  const r = Math[f](v);
  return Math.abs(r) < 1e-15 ? 0 : r;
}

function evaluate(n: Node, env: Env): number {
  switch (n.t) {
    case "num":
      return n.v;
    case "var":
      return n.n === "x" ? (env.x ?? NaN) : (env.ans ?? 0);
    case "neg":
      return -evaluate(n.a, env);
    case "fact":
      return factorial(evaluate(n.a, env));
    case "bin": {
      const a = evaluate(n.a, env);
      const b = evaluate(n.b, env);
      switch (n.op) {
        case "+":
          return a + b;
        case "-":
          return a - b;
        case "*":
          return a * b;
        case "/":
          return a / b;
        case "^":
          // real powers of negatives with an odd denominator: (-8)^(1/3) = -2, (-8)^(2/3) = 4
          if (a < 0 && Number.isFinite(b) && !Number.isInteger(b)) {
            const f = smallFraction(b);
            if (f && f.q % 2 !== 0) return (f.p % 2 !== 0 ? -1 : 1) * Math.pow(-a, b);
          }
          return Math.pow(a, b);
      }
      return NaN;
    }
    case "call": {
      const v = evaluate(n.a, env);
      const angle = env.angle ?? "rad";
      const toAngle = (r: number) => (angle === "deg" ? (r * 180) / Math.PI : r);
      switch (n.f) {
        case "sin":
        case "cos":
        case "tan":
          return trig(n.f, v, angle);
        case "asin":
          return toAngle(Math.asin(v));
        case "acos":
          return toAngle(Math.acos(v));
        case "atan":
          return toAngle(Math.atan(v));
        case "sqrt":
          return Math.sqrt(v);
        case "log":
          return Math.log10(v);
        case "ln":
          return Math.log(v);
        case "abs":
          return Math.abs(v);
        case "exp":
          return Math.exp(v);
      }
    }
  }
  return NaN;
}

/** Parse once, evaluate many times (the graph calls it for every pixel column). */
export function compile(src: string, opts: ParseOptions = {}): (env: Env) => number {
  const tree = parse(src, opts);
  return (env) => evaluate(tree, env);
}

/** Parse and evaluate. Throws CalcError on a syntax problem; may return NaN / ±Infinity. */
export function calculate(src: string, env: Env = {}): number {
  return compile(src, { x: env.x !== undefined })(env);
}

/** Up to 12 significant digits, no float noise (0.1+0.2 -> "0.3"); exponent form outside
 *  1e-9 .. 1e15 ("1.5e+21" -> "1.5×10^21"). */
export function formatResult(v: number): string {
  if (Number.isNaN(v)) return "Not a number";
  if (!Number.isFinite(v)) return v > 0 ? "Too large" : "Too small";
  if (v === 0) return "0";
  const abs = Math.abs(v);
  if (abs >= 1e15 || abs < 1e-9) {
    const [m, e] = v.toExponential(10).split("e");
    const mant = String(Number(m));
    return `${mant}×10^${Number(e)}`;
  }
  return String(Number(v.toPrecision(12)));
}
