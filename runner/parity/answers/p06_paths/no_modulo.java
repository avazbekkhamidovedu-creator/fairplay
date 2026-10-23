import java.io.*;
import java.util.*;

public class Main {
    public static void main(String[] args) throws IOException {
        BufferedReader br = new BufferedReader(new InputStreamReader(System.in));
        StringTokenizer st = new StringTokenizer(br.readLine());
        int r = Integer.parseInt(st.nextToken()), c = Integer.parseInt(st.nextToken());
        long[] dp = new long[c];
        for (int i = 0; i < r; i++) {
            String row = br.readLine().trim();
            for (int j = 0; j < c; j++) {
                if (row.charAt(j) == '#') dp[j] = 0;
                else if (i == 0 && j == 0) dp[j] = 1;
                else if (j > 0) dp[j] = dp[j] + dp[j - 1];
            }
        }
        System.out.println(dp[c - 1]);
    }
}
