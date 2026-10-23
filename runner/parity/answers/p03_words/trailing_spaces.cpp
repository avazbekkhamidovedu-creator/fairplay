#include <bits/stdc++.h>
using namespace std;
int main() {
    map<string, int> cnt;
    string w;
    while (cin >> w) {
        for (char &ch : w) ch = (char)tolower((unsigned char)ch);
        cnt[w]++;
    }
    vector<pair<string, int>> v(cnt.begin(), cnt.end());
    stable_sort(v.begin(), v.end(), [](const auto &a, const auto &b) { return a.second > b.second; });
    for (auto &p : v) cout << p.first << ' ' << p.second << "   \n";
}
