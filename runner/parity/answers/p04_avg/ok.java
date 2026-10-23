import java.io.*;
import java.util.*;

public class Main {
    public static void main(String[] args) throws IOException {
        BufferedReader br = new BufferedReader(new InputStreamReader(System.in));
        int n = Integer.parseInt(br.readLine().trim());
        StringTokenizer st = new StringTokenizer(br.readLine());
        long s = 0;
        for (int i = 0; i < n; i++) s += Long.parseLong(st.nextToken());
        System.out.println(String.format(Locale.ROOT, "%.2f", (double) s / n));
    }
}
