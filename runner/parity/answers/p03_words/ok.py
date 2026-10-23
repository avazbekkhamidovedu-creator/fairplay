import sys
from collections import Counter
cnt = Counter(w.lower() for w in sys.stdin.read().split())
for w, c in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0])):
    print(w, c)
