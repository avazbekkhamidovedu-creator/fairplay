import java.io.*;

public class Main {
    public static void main(String[] args) throws IOException {
        BufferedReader br = new BufferedReader(new InputStreamReader(System.in));
        int t = Integer.parseInt(br.readLine().trim());
        StringBuilder out = new StringBuilder();
        while (t-- > 0) {
            String s = br.readLine().trim();
            char[] st = new char[s.length()];
            int top = 0; boolean ok = true;
            for (int i = 0; i < s.length() && ok; i++) {
                char ch = s.charAt(i);
                if (ch == '(' || ch == '[' || ch == '{') st[top++] = ch;
                else {
                    char want = ch == ')' ? '(' : ch == ']' ? '[' : '{';
                    if (top == 0 || st[top - 1] != want) ok = false;
                    else top--;
                }
            }
            out.append(ok && top == 0 ? "YES" : "NO").append('\n');
        }
        System.out.print(out);
    }
}
