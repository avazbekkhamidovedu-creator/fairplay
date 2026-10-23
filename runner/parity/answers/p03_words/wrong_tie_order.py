import sys
cnt = {}
for w in sys.stdin.read().split():
    w = w.lower()
    cnt[w] = cnt.get(w, 0) + 1
for w, c in sorted(cnt.items(), key=lambda kv: -kv[1]):
    print(w, c)
