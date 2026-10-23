import java.io.*;
import java.util.*;

public class Main {
    public static void main(String[] args) throws IOException {
        BufferedReader br = new BufferedReader(new InputStreamReader(System.in));
        Map<String, Integer> cnt = new HashMap<>();
        String line;
        while ((line = br.readLine()) != null) {
            StringTokenizer st = new StringTokenizer(line);
            while (st.hasMoreTokens()) cnt.merge(st.nextToken().toLowerCase(Locale.ROOT), 1, Integer::sum);
        }
        List<Map.Entry<String, Integer>> v = new ArrayList<>(cnt.entrySet());
        v.sort((a, b) -> !a.getValue().equals(b.getValue()) ? b.getValue() - a.getValue() : a.getKey().compareTo(b.getKey()));
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, Integer> e : v) sb.append(e.getKey()).append(' ').append(e.getValue()).append('\n');
        System.out.print(sb);
    }
}
