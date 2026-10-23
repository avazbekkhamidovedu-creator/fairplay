import sys
lines = sys.stdin.read().split()
t = int(lines[0])
pairs = {')': '(', ']': '[', '}': '{'}
out = []
for s in lines[1:1 + t]:
    st = []
    ok = True
    for ch in s:
        if ch in '([{':
            st.append(ch)
        elif not st or st.pop() != pairs[ch]:
            ok = False
            break
    out.append("YES" if ok and not st else "NO")
print("\n".join(out))
