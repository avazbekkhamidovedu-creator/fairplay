import sys
cnt = {}
for w in sys.stdin.read().split()
    cnt[w.lower()] = cnt.get(w.lower(), 0) + 1
