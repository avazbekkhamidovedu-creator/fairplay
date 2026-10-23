#include <bits/stdc++.h>
using namespace std;
int main() {
    int r, c; cin >> r >> c;
    const long long MOD = 1000000007LL;
    vector<long long> dp(c, 0);
    string row;
    for (int i = 0; i < r; i++) {
        cin >> row;
        for (int j = 0; j < c; j++) {
            if (row[j] == '#') dp[j] = 0;
            else if (i == 0 && j == 0) dp[j] = 1;
            else if (j > 0) dp[j] = (dp[j] + dp[j - 1]) % MOD;
        }
    }
    cout << dp[c - 1] << "\n";
}
