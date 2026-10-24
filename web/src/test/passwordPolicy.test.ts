import { describe, expect, it } from "vitest";
import { PASSWORD_RULES, checkPassword, hasRepeats, hasSequence, strength } from "../lib/passwordPolicy";

const ok = (pw: string, ctx = {}) => checkPassword(pw, ctx).ok;
const failed = (pw: string, ctx = {}) => checkPassword(pw, ctx).results.filter((r) => !r.ok).map((r) => r.id);

describe("password policy", () => {
  it("lists the six rules in order", () => {
    expect(PASSWORD_RULES.map((r) => r.id)).toEqual(["length", "lower", "upper", "digit", "symbol", "personal"]);
  });

  it("accepts a password that meets every rule", () => {
    expect(ok("Correct-horse-9")).toBe(true);
    expect(ok("Aa1!aaaaaa")).toBe(true);
    expect(checkPassword("Correct-horse-9").message).toBeNull();
  });

  it("checks each rule on its own", () => {
    expect(failed("Aa1!aaaaa")).toEqual(["length"]);
    expect(failed("AA1!AAAAAA")).toEqual(["lower"]);
    expect(failed("aa1!aaaaaa")).toEqual(["upper"]);
    expect(failed("Aab!aaaaaa")).toEqual(["digit"]);
    expect(failed("Aa1aaaaaaa")).toEqual(["symbol"]);
    expect(failed("")).toEqual(["length", "lower", "upper", "digit", "symbol", "personal"]);
  });

  it("a space is not a symbol", () => {
    expect(failed("Aa1 aaaaaa")).toEqual(["symbol"]);
    expect(ok("Aa1 aaaaa_")).toBe(true);
  });

  it("works with non-ASCII letters and symbols", () => {
    expect(ok("Ünïcödé-12")).toBe(true);
    expect(failed("пароль-123")).toEqual(["upper"]);
    expect(ok("Пароль-123")).toBe(true);
  });

  it("may not equal the username or NetID, case-insensitive", () => {
    expect(failed("Bob.Lee-2026", { username: "bob.lee-2026" })).toEqual(["personal"]);
    expect(failed("Bl642-Abc!x", { netid: "bl642-abc!X" })).toEqual(["personal"]);
    expect(checkPassword("Bl642-Abc!x", { netid: "bl642-abc!X" }).message).toBe("Your password can't be your username or NetID.");
    expect(ok("Bl642-Abc!x", { username: "", netid: "" })).toBe(true);
  });

  it("message lists what is missing", () => {
    expect(checkPassword("abcdefghij").message).toBe("Password needs an upper-case letter, a digit and a symbol.");
    expect(checkPassword("Abc1!").message).toBe("Password needs at least 10 characters.");
  });

  it("detects sequences and repeats", () => {
    expect(hasSequence("xx1234yy")).toBe(true);
    expect(hasSequence("ABCD")).toBe(true);
    expect(hasSequence("zyxw")).toBe(true);
    expect(hasSequence("1357abd")).toBe(false);
    expect(hasRepeats("aaa")).toBe(true);
    expect(hasRepeats("aab")).toBe(false);
  });

  it("strength: 0 empty, weak when a rule fails, higher with length and variety", () => {
    expect(strength("").level).toBe(0);
    expect(strength("abc").label).toBe("Weak");
    expect(strength("correct horse battery staple").level).toBe(1); // breaks rules
    expect(strength("Aa1!aaaaaa").level).toBeLessThan(strength("Tulip-Ocean-73!x").level);
    expect(strength("Tulip-Ocean-73!x")).toEqual({ level: 4, label: "Strong" });
    expect(strength("Kq7!mzp2Rw").level).toBe(3);
  });

  it("strength: penalties for sequences, repeats, username and common words", () => {
    const base = strength("Kq7!mzp2Rwx4Tvb9").level;
    expect(strength("Kq7!1234Rwx4Tvb9").level).toBeLessThan(base);
    expect(strength("Kq7!mzzzRwx4Tvb9").level).toBeLessThan(base);
    expect(strength("Kq7!boblee-X", { username: "boblee" }).level).toBeLessThan(strength("Kq7!mzpqwe-X").level);
    expect(strength("Password-2026!").level).toBeLessThan(strength("Pinecone-2026!").level);
  });
});
