import sys
data = sys.stdin.read().split()
n = int(data[0])
s = sum(map(int, data[1:1 + n]))
print(round(s / n, 2))
