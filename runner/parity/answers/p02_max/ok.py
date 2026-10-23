import sys
data = sys.stdin.read().split()
n = int(data[0])
arr = list(map(int, data[1:1 + n]))
m = max(arr)
print(m, arr.index(m) + 1)
