import sys
data = sys.stdin.buffer.read().split()
big = [i for i in range(50_000_000)]
print(len(big) + len(data))
