import java.io.*;
import java.util.*;

public class Main {
    public static void main(String[] args) throws IOException {
        BufferedReader br = new BufferedReader(new InputStreamReader(System.in));
        int n = Integer.parseInt(br.readLine().trim());
        StringTokenizer st = new StringTokenizer(br.readLine());
        long best = 0; int pos = 0;
        for (int i = 1; i <= n; i++) {
            long x = Long.parseLong(st.nextToken());
            if (i == 1 || x > best) { best = x; pos = i; }
        }
        System.out.print(best + " " + pos);
    }
}
