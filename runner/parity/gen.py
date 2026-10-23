#!/usr/bin/env python3
"""Generate the parity problems (DMOJ problem-data format) with seeded, reproducible test data.

    python3 gen.py            # writes problems/<id>/{init.yml, N.in, N.out} and problems/<id>/meta.yml

Every expected output comes from the reference solution below, never typed by hand.
meta.yml holds what the DMOJ site would normally store on the problem: name, time and memory limits.
"""
import os
import random
import string
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "problems")
MOD = 10**9 + 7


def write_problem(pid, name, tl, ml_mb, cases, statement):
    d = os.path.join(OUT, pid)
    os.makedirs(d, exist_ok=True)
    lines = ["test_cases:"]
    total = len(cases)
    base = 100 // total
    for i, (inp, out) in enumerate(cases, 1):
        with open(os.path.join(d, f"{i}.in"), "w", newline="\n") as f:
            f.write(inp)
        with open(os.path.join(d, f"{i}.out"), "w", newline="\n") as f:
            f.write(out)
        pts = base + (100 - base * total if i == total else 0)
        lines.append(f"- {{in: {i}.in, out: {i}.out, points: {pts}}}")
    with open(os.path.join(d, "init.yml"), "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(d, "meta.yml"), "w") as f:
        f.write(f"name: {name}\ntime_limit_s: {tl}\nmemory_limit_mb: {ml_mb}\n")
    with open(os.path.join(d, "statement.md"), "w") as f:
        f.write(f"# {name}\n\n{statement.strip()}\n\nTime limit {tl} s, memory limit {ml_mb} MB.\n")


# ---- p01: A + B with 64-bit values (int overflow -> WA) ----
def p01():
    rng = random.Random(101)
    cases = []
    pairs = [(3, 4), (-5, 12), (2_000_000_000, 2_000_000_000), (10**18, -(10**17))]
    pairs.append((rng.randint(-10**18, 10**18), rng.randint(-10**18, 10**18)))
    for a, b in pairs:
        cases.append((f"{a} {b}\n", f"{a + b}\n"))
    write_problem("p01_sum", "Big sum", 1, 256, cases,
                  "Read two integers a and b (|a|, |b| <= 10^18) on one line. Print a + b.")


# ---- p02: maximum and its first position ----
def p02():
    rng = random.Random(202)
    cases = []
    for n in (1, 5, 1000, 200_000):
        arr = [rng.randint(-10**9, 10**9) for _ in range(n)]
        if n == 5:
            arr = [4, 9, 2, 9, 1]
        m = max(arr)
        cases.append((f"{n}\n{' '.join(map(str, arr))}\n", f"{m} {arr.index(m) + 1}\n"))
    write_problem("p02_max", "Maximum and position", 1, 256, cases,
                  "Read N (1 <= N <= 200000), then N integers. Print the maximum and the 1-based position "
                  "of its first occurrence, separated by a space.")


# ---- p03: word frequencies (sorting rules, output format) ----
def p03_solve(text):
    cnt = {}
    for w in text.split():
        w = w.lower()
        cnt[w] = cnt.get(w, 0) + 1
    items = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))
    return "".join(f"{w} {c}\n" for w, c in items)


def p03():
    rng = random.Random(303)
    cases = []
    texts = ["The cat and the hat\nAND the bat\n"]
    vocab = ["".join(rng.choice(string.ascii_letters) for _ in range(rng.randint(1, 6))) for _ in range(300)]
    for lines in (20, 2000):
        texts.append("".join(" ".join(rng.choice(vocab) for _ in range(rng.randint(1, 15))) + "\n"
                             for _ in range(lines)))
    for t in texts:
        cases.append((t, p03_solve(t)))
    write_problem("p03_words", "Word count", 1, 256, cases,
                  "Read text until end of input. Words are separated by whitespace and compared ignoring case. "
                  "Print each distinct word in lowercase with its count, most frequent first; equal counts in "
                  "alphabetical order. One word per line: `word count`.")


# ---- p04: average with exactly 2 decimals (formatting) ----
def p04():
    rng = random.Random(404)
    cases = []
    sizes = [3, 10, 1000, 100_000]
    for n in sizes:
        while True:
            arr = [rng.randint(-10**6, 10**6) for _ in range(n)]
            if n == 3:
                arr = [1, 2, 4]
            avg = Fraction(sum(arr), n)
            scaled = avg * 100
            frac = scaled - (scaled.numerator // scaled.denominator)
            # avoid exact ...5 ties, so every language's rounding agrees
            s = f"{float(avg):.2f}"
            # avoid "-0.00", which languages print differently
            if frac != Fraction(1, 2) and s != "-0.00":
                break
        cases.append((f"{n}\n{' '.join(map(str, arr))}\n", s + "\n"))
    write_problem("p04_avg", "Average", 1, 256, cases,
                  "Read N (1 <= N <= 100000), then N integers. Print their average with exactly two digits "
                  "after the decimal point.")


# ---- p05: count primes up to N (naive -> TLE) ----
def p05_solve(n):
    if n < 2:
        return 0
    sieve = bytearray([1]) * (n + 1)
    sieve[0] = sieve[1] = 0
    i = 2
    while i * i <= n:
        if sieve[i]:
            sieve[i * i::i] = bytearray(len(range(i * i, n + 1, i)))
        i += 1
    return sum(sieve)


def p05():
    cases = [(f"{n}\n", f"{p05_solve(n)}\n") for n in (1, 10, 100_000, 5_000_000)]
    write_problem("p05_primes", "Counting primes", 1, 256, cases,
                  "Read N (1 <= N <= 5000000). Print how many primes are <= N.")


# ---- p06: grid paths mod 1e9+7 (missing mod -> WA) ----
def p06_solve(grid):
    r, c = len(grid), len(grid[0])
    dp = [0] * c
    for i in range(r):
        for j in range(c):
            if grid[i][j] == "#":
                dp[j] = 0
            elif i == 0 and j == 0:
                dp[j] = 1
            elif j > 0:
                dp[j] = (dp[j] + dp[j - 1]) % MOD
    return dp[c - 1]


def p06():
    rng = random.Random(606)
    cases = []
    for r, c, p in ((3, 3, 0.0), (4, 5, 0.2), (60, 60, 0.05), (1000, 1000, 0.02)):
        grid = ["".join("#" if rng.random() < p else "." for _ in range(c)) for _ in range(r)]
        grid[0] = "." + grid[0][1:]
        grid[-1] = grid[-1][:-1] + "."
        cases.append((f"{r} {c}\n" + "\n".join(grid) + "\n", f"{p06_solve(grid)}\n"))
    write_problem("p06_paths", "Grid paths", 2, 256, cases,
                  "Read R and C (1 <= R, C <= 1000), then R lines of C characters ('.' free, '#' blocked). "
                  "Moving only right or down from the top-left to the bottom-right cell, print the number of "
                  "paths modulo 1000000007.")


# ---- p07: balanced brackets (deep nesting -> recursion RTE) ----
def p07_ok(s):
    pairs = {")": "(", "]": "[", "}": "{"}
    st = []
    for ch in s:
        if ch in "([{":
            st.append(ch)
        else:
            if not st or st[-1] != pairs[ch]:
                return False
            st.pop()
    return not st


def p07():
    rng = random.Random(707)
    cases = []
    sets = [["()", "([)]", "{[]}", "(("]]
    rand = []
    for _ in range(50):
        n = rng.randint(1, 40)
        rand.append("".join(rng.choice("()[]{}") for _ in range(n)))
    sets.append(rand)
    deep = "(" * 50_000 + ")" * 50_000
    sets.append([deep, deep[:-1] + "]", "[" * 30_000 + "]" * 30_000])
    for lines in sets:
        inp = f"{len(lines)}\n" + "\n".join(lines) + "\n"
        out = "".join("YES\n" if p07_ok(s) else "NO\n" for s in lines)
        cases.append((inp, out))
    write_problem("p07_brackets", "Balanced brackets", 1, 256, cases,
                  "Read T, then T lines each made of ()[]{} (length 1 to 100000). For each line print YES if "
                  "the brackets are balanced and NO otherwise.")


# ---- p08: sort many numbers (slow input -> TLE) ----
def p08():
    rng = random.Random(808)
    cases = []
    for n in (1, 10, 100_000, 1_000_000):
        arr = [rng.randint(-10**9, 10**9) for _ in range(n)]
        inp = f"{n}\n" + "\n".join(map(str, arr)) + "\n"
        cases.append((inp, " ".join(map(str, sorted(arr))) + "\n"))
    write_problem("p08_sort", "Sorting", 2, 256, cases,
                  "Read N (1 <= N <= 1000000), then N integers, one per line. Print them in non-decreasing "
                  "order on one line, separated by spaces.")


if __name__ == "__main__":
    for f in (p01, p02, p03, p04, p05, p06, p07, p08):
        f()
    print("problems written to", OUT)
