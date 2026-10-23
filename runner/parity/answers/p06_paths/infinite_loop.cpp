#include <cstdio>
int main() {
    int r, c; scanf("%d %d", &r, &c);
    volatile unsigned long long x = 0;
    while (true) x++;
    printf("%d\n", r + c);
}
