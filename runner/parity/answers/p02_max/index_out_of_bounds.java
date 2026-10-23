import java.io.*;
import java.util.*;

public class Main {
    public static void main(String[] args) throws IOException {
        BufferedReader br = new BufferedReader(new InputStreamReader(System.in));
        int n = Integer.parseInt(br.readLine().trim());
        long[] a = new long[n];
        StringTokenizer st = new StringTokenizer(br.readLine());
        for (int i = 0; i < n; i++) a[i] = Long.parseLong(st.nextToken());
        int pos = 0;
        for (int i = 0; i <= n; i++) if (a[i] > a[pos]) pos = i;
        System.out.println(a[pos] + " " + (pos + 1));
    }
}
