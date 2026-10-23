#include <bits/stdc++.h>
using namespace std;
int main() {
    ios::sync_with_stdio(false); cin.tie(nullptr);
    int t; cin >> t;
    string s;
    while (t--) {
        cin >> s;
        string st; bool ok = true;
        for (char ch : s) {
            if (ch == '(' || ch == '[' || ch == '{') st.push_back(ch);
            else {
                char want = ch == ')' ? '(' : ch == ']' ? '[' : '{';
                if (st.empty() || st.back() != want) { ok = false; break; }
                st.pop_back();
            }
        }
        cout << (ok && st.empty() ? "YES" : "NO") << '\n';
    }
}
