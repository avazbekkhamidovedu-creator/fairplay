#include <cstdio>
int main() {
    int n;
    scanf("%d", &n);
    long long best = 0; int pos = 0;
    for (int i = 1; i <= n; i++) {
        long long x; scanf("%lld", &x);
        if (i == 1 || x > best) { best = x; pos = i; }
    }
    printf("%lld %d\n", best, pos);
}
