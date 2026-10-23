#include <bits/stdc++.h>
using namespace std;
int main() {
    ios::sync_with_stdio(false); cin.tie(nullptr);
    map<string, int> cnt;
    string w;
    while (cin >> w) {
        for (char &ch : w) ch = (char)tolower((unsigned char)ch);
        cnt[w]++;
    }
    vector<pair<string, int>> v(cnt.begin(), cnt.end());
    sort(v.begin(), v.end(), [](const auto &a, const auto &b) {
        if (a.second != b.second) return a.second > b.second;
        return a.first < b.first;
    });
    for (auto &p : v) cout << p.first << ' ' << p.second << '\n';
}
