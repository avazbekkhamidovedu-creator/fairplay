#include <cstdio>
#include <vector>
int main() {
    int n; scanf("%d", &n);
    std::vector<char> comp(n + 1, 0);
    int count = 0;
    for (long long i = 2; i <= n; i++) {
        if (comp[i]) continue;
        count++;
        for (long long j = i * i; j <= n; j += i) comp[j] = 1;
    }
    printf("%d\n", count);
}
