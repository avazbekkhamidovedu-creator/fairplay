#include <cstdio>
int main() {
    int n; scanf("%d", &n);
    long long s = 0;
    for (int i = 0; i < n; i++) { long long x; scanf("%lld", &x); s += x; }
    printf("%.2f\n", (double)(s / n));
}
