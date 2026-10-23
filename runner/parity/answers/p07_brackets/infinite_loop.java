public class Main {
    public static void main(String[] args) {
        long x = 0;
        while (x >= 0) x = (x + 1) % 1000;
        System.out.println(x);
    }
}
