#include <cstdio>
#include <vector>
int main() {
    int n; scanf("%d", &n);
    std::vector<long long> a(n);
    for (auto &x : a) scanf("%lld", &x);
    for (int i = 0; i < n; i++)
        for (int j = 0; j + 1 < n - i; j++)
            if (a[j] > a[j + 1]) { long long t = a[j]; a[j] = a[j + 1]; a[j + 1] = t; }
    for (int i = 0; i < n; i++) printf(i ? " %lld" : "%lld", a[i]);
    printf("\n");
}
