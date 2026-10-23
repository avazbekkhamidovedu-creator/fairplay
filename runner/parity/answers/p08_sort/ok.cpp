#include <bits/stdc++.h>
using namespace std;
int main() {
    ios::sync_with_stdio(false); cin.tie(nullptr);
    int n; cin >> n;
    vector<long long> a(n);
    for (auto &x : a) cin >> x;
    sort(a.begin(), a.end());
    string out;
    out.reserve((size_t)n * 12);
    for (int i = 0; i < n; i++) { if (i) out += ' '; out += to_string(a[i]); }
    out += '\n';
    fwrite(out.data(), 1, out.size(), stdout);
}
