import java.util.Scanner;

public class Main {
    public static void main(String[] args) {
        int n = new Scanner(System.in).nextInt();
        long[] big = new long[100_000_000];
        for (int i = 0; i < big.length; i += 4096) big[i] = i;
        System.out.println(big[n % 4096] + n);
    }
}
