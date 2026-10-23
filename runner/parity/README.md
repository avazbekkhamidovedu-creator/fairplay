# DKU OJ parity kit (Code Runner, phase 1)

The first acceptance test of the code runner: the same answers go through DKU OJ's judge and through
our runner (Piston or Judge0), and every verdict, score and limit must match.

## What is here

| Path | What it is |
|---|---|
| `gen.py` | Generates 8 DKU-style problems in DMOJ problem-data format (`init.yml` + test files), seeded so the data is identical on every machine. Expected outputs come from reference solutions, never typed by hand. |
| `problems/<id>/` | Generated: `init.yml`, `N.in` / `N.out`, `meta.yml` (time and memory limits), `statement.md`. |
| `answers/<id>/` | 50 answers in Java, Python and C++: a correct one per problem and language, plus answers built to fail in one specific way. |
| `expected.yml` | What each answer is designed to get, with a note on what it checks. |
| `judge_dmoj.py`, `run-baseline.sh`, `parity-judge.yml` | Judge every answer on the real DMOJ judge (`dmoj` from PyPI, the judge DKU OJ runs) and write `results/dmoj-baseline.json`. |

## Problems

| Id | Problem | What it tests |
|---|---|---|
| p01_sum | a + b up to 10^18 | 64-bit integers, compile errors, output flood |
| p02_max | maximum and its position | off-by-one, missing final newline, Java exception |
| p03_words | word frequencies | sorting rules, trailing spaces, Python syntax error |
| p04_avg | average with 2 decimals | number formatting, integer division, Python exception |
| p05_primes | count primes up to 5,000,000 | time limit, memory limit in C++ and Java |
| p06_paths | grid paths mod 10^9+7 | overflow, pure-Python speed, infinite loop |
| p07_brackets | balanced brackets | deep recursion, segmentation fault, Java infinite loop |
| p08_sort | sort 1,000,000 numbers | input/output speed, memory, O(n^2) time-outs |

Every problem has 3 to 5 tests with points, so partial scores are compared too.

## Run the baseline

```
python3 gen.py            # once; regenerates problems/
sudo bash run-baseline.sh # judges all answers one at a time on the DMOJ judge
```

`--only p05` judges a subset. Run it on a quiet machine: time limits are real, and a busy machine
turns a pass into a time-out.

## What still differs from DKU OJ production

The baseline uses this machine's compilers (g++ 13, Python 3.13, OpenJDK 21) with DMOJ's default
flags. DKU OJ's production versions, flags and per-language time multipliers must be read from the
live site (its runtimes / judges page) and copied into `parity-judge.yml` before the comparison
counts; harness problems (DKU's Java/Python function harnesses) are a second step.
