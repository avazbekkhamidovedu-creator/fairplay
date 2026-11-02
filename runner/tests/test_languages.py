import pytest

from app.config import ConfigError, Settings, parse_tenants
from app.languages import detect_java_class, java_xmx_mb


@pytest.mark.parametrize("src,name", [
    ("public class Main { }", "Main"),
    ("import java.util.*;\npublic class Solve { public static void main(String[] a){} }", "Solve"),
    ("class Helper {}\npublic class Main {}", "Main"),
    ("class First { }\nclass Second { }", "First"),
    ("// public class Fake {}\n/* public class Fake2 {} */\nclass Real {}", "Real"),
    ('class A { String s = "public class Nope {}"; }', "A"),
    ("public final class Z {}", "Z"),
    ("public record P(int x) {}", "P"),
    ("class Outer { public static class Inner {} }", "Outer"),
    ("public interface I {}", "I"),
    ('class T { String t = """\n public class Bad {}\n"""; }', "T"),
])
def test_detect_ok(src, name):
    r = detect_java_class(src)
    assert r.error is None and r.name == name


@pytest.mark.parametrize("src", [
    "package foo;\npublic class Main {}",
    "int x = 3;",
    "// class Main {}",
    "",
])
def test_detect_errors(src):
    r = detect_java_class(src)
    assert r.name is None and "public class Main" in r.error


def test_xmx_leaves_headroom():
    assert java_xmx_mb(262144) < 256
    assert java_xmx_mb(1048576) == 1024 - 96
    assert java_xmx_mb(16384) >= 8


def test_tenants_parse_and_secret_length():
    assert parse_tenants("a:" + "x" * 32 + ",b:" + "y" * 40) == {"a": "x" * 32, "b": "y" * 40}
    with pytest.raises(ConfigError):
        parse_tenants("a:short")
    with pytest.raises(ConfigError):
        parse_tenants("")
    with pytest.raises(ConfigError):
        Settings.from_env({"RUNNER_TENANTS": "a:short"})
