import java.io.*;
import java.util.*;

public class Main {
    public static void main(String[] args) throws IOException {
        DataInputStream in = new DataInputStream(new BufferedInputStream(System.in, 1 << 16));
        int n = (int) readLong(in);
        long[] a = new long[n];
        for (int i = 0; i < n; i++) a[i] = readLong(in);
        Arrays.sort(a);
        StringBuilder sb = new StringBuilder(n * 12);
        for (int i = 0; i < n; i++) { if (i > 0) sb.append(' '); sb.append(a[i]); }
        sb.append('\n');
        PrintWriter out = new PrintWriter(new BufferedWriter(new OutputStreamWriter(System.out)));
        out.print(sb);
        out.flush();
    }

    static long readLong(DataInputStream in) throws IOException {
        int b = in.read();
        while (b != '-' && (b < '0' || b > '9')) b = in.read();
        boolean neg = b == '-';
        if (neg) b = in.read();
        long r = 0;
        while (b >= '0' && b <= '9') { r = r * 10 + (b - '0'); b = in.read(); }
        return neg ? -r : r;
    }
}
