import java.util.Scanner;

public class Main {
    public static void main(String[] args) {
        int n = new Scanner(System.in).nextInt();
        boolean[] comp = new boolean[n + 1];
        int count = 0;
        for (long i = 2; i <= n; i++) {
            if (comp[(int) i]) continue;
            count++;
            for (long j = i * i; j <= n; j += i) comp[(int) j] = true;
        }
        System.out.println(count);
    }
}
