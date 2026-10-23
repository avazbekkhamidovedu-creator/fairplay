import sys
data = sys.stdin.buffer.read().split()
n = int(data[0])
arr = sorted(map(int, data[1:1 + n]))
sys.stdout.write(" ".join(map(str, arr)) + "\n")
