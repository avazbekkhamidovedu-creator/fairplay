#include <cstdio>
#include <cstring>
#include <cstdlib>
int main() {
    int n; scanf("%d", &n);
    size_t size = 400u * 1024u * 1024u;
    char *big = (char *)malloc(size);
    if (!big) return 3;
    memset(big, 1, size);
    printf("%d\n", big[n % 100] + n);
}
