import re
from dataclasses import dataclass

LANGUAGES = ("c", "cpp", "java")

JAVA_STACK = "64m"
COMPILE_MEMORY_MB = 512
COMPILE_OUTPUT_CAP = 64 * 1024
COMPILE_OUTPUT_RETURN = 8000

NO_PUBLIC_CLASS_MSG = "Define a public class Main; do not use a package."


@dataclass(frozen=True)
class JavaDetect:
    name: str | None
    error: str | None


def _strip_java(src: str) -> str:
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == "//":
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif two == "/*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif src.startswith('"""', i):
            j = i + 3
            while j < n and not (src[j] == '"' and src.startswith('"""', j)):
                j += 2 if src[j] == "\\" else 1
            i = min(n, j + 3)
            out.append(" ")
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            i = min(n, j + 1)
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _top_level(text: str) -> str:
    depth = 0
    out = []
    for c in text:
        if c == "{":
            depth += 1
            out.append(" ")
        elif c == "}":
            depth = max(0, depth - 1)
            out.append(" ")
        else:
            out.append(c if depth == 0 else " ")
    return "".join(out)


_DECL = re.compile(
    r"(?<![\w$@])((?:(?:public|protected|private|abstract|final|strictfp|sealed|non-sealed|static)\s+)*)"
    r"(class|interface|enum|record)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_PACKAGE = re.compile(r"(?m)^\s*package\s+[\w.$]+\s*;")


def detect_java_class(source: str) -> JavaDetect:
    code = _strip_java(source)
    if _PACKAGE.search(code):
        return JavaDetect(None, NO_PUBLIC_CLASS_MSG)
    top = _top_level(code)
    first = None
    for m in _DECL.finditer(top):
        mods = m.group(1).split()
        if "public" in mods:
            return JavaDetect(m.group(3), None)
        if first is None:
            first = m.group(3)
    if first:
        return JavaDetect(first, None)
    return JavaDetect(None, NO_PUBLIC_CLASS_MSG)


def java_xmx_mb(limit_kb: int) -> int:
    limit_mb = limit_kb // 1024
    headroom = min(96, int(limit_mb * 0.4))
    return max(8, limit_mb - headroom)


def compile_argv(lang: str, java_class: str | None = None) -> tuple[str, list[str]]:
    if lang == "c":
        return "main.c", ["gcc", "-O2", "-std=c17", "-fmax-errors=5", "-s", "-o", "main", "main.c", "-lm"]
    if lang == "cpp":
        return "main.cpp", ["g++", "-O2", "-std=c++20", "-fmax-errors=5", "-s", "-o", "main", "main.cpp"]
    if lang == "java":
        assert java_class
        return f"{java_class}.java", [
            "javac", "-encoding", "UTF-8", "-nowarn",
            "-J-XX:+UseSerialGC", "-J-XX:TieredStopAtLevel=1", "-J-Xshare:auto",
            f"{java_class}.java",
        ]
    raise ValueError(lang)


def run_argv(lang: str, memory_limit_kb: int, java_class: str | None = None) -> list[str]:
    if lang in ("c", "cpp"):
        return ["./main"]
    return [
        "java",
        f"-Xmx{java_xmx_mb(memory_limit_kb)}m", "-Xms8m", f"-Xss{JAVA_STACK}",
        "-XX:+UseSerialGC", "-XX:ActiveProcessorCount=1", "-XX:TieredStopAtLevel=1",
        "-XX:ReservedCodeCacheSize=32m", "-XX:-UsePerfData", "-XX:+ExitOnOutOfMemoryError",
        "-Xshare:auto", "-Dfile.encoding=UTF-8", "-Dstdout.encoding=UTF-8",
        "-Dstderr.encoding=UTF-8", "-Dsun.jnu.encoding=UTF-8", "-Djava.io.tmpdir=/tmp",
        "-cp", "/work", java_class or "Main",
    ]
