n = int(input())
count = 0
for x in range(2, n + 1):
    d = 2
    prime = True
    while d * d <= x:
        if x % d == 0:
            prime = False
            break
        d += 1
    if prime:
        count += 1
print(count)
