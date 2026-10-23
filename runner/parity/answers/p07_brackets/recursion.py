import sys
lines = sys.stdin.read().split()
t = int(lines[0])
pairs = {'(': ')', '[': ']', '{': '}'}


def parse(s, i):
    # parses a sequence starting at i, returns the index after it, or -1
    while i < len(s) and s[i] in pairs:
        j = parse(s, i + 1)
        if j < 0 or j >= len(s) or s[j] != pairs[s[i]]:
            return -1
        i = j + 1
    return i


for s in lines[1:1 + t]:
    print("YES" if parse(s, 0) == len(s) else "NO")
