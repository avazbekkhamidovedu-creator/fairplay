import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        int n = in.nextInt();
        long[] a = new long[n];
        for (int i = 0; i < n; i++) a[i] = in.nextLong();
        Arrays.sort(a);
        for (int i = 0; i < n; i++) System.out.print((i > 0 ? " " : "") + a[i]);
        System.out.println();
    }
}
