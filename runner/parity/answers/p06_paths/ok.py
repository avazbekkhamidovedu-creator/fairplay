import sys
data = sys.stdin.read().split()
r, c = int(data[0]), int(data[1])
grid = data[2:2 + r]
MOD = 10**9 + 7
dp = [0] * c
for i in range(r):
    row = grid[i]
    left = 0
    for j in range(c):
        if row[j] == '#':
            v = 0
        elif i == 0 and j == 0:
            v = 1
        else:
            v = dp[j] + left
            if v >= MOD:
                v -= MOD
        dp[j] = v
        left = v
print(dp[c - 1])
