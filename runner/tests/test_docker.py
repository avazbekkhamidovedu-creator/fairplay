import asyncio
import os
import subprocess

import pytest

from app.config import Settings
from app.executor import DockerExecutor
from tests.conftest import needs_docker

pytestmark = needs_docker

COMPILE_TIMEOUT = int(os.environ.get("RUNNER_TEST_COMPILE_TIMEOUT_S", "10"))
JAVA_STARTUP = int(os.environ.get("RUNNER_TEST_JAVA_STARTUP_MS", "400"))
SLOW = int(os.environ.get("RUNNER_TEST_TIME_MS", "2000"))


def make_exec(pool=0):
    s = Settings(tenants={"t": "x" * 32}, warm_pool=pool, compile_timeout_s=COMPILE_TIMEOUT,
                 java_startup_ms=JAVA_STARTUP)
    return DockerExecutor(s)


def run(lang, source, stdins=("",), t=SLOW, mem=262144):
    return asyncio.run(make_exec().run(lang, source, list(stdins), t, mem))


def leftovers():
    out = subprocess.run(["docker", "ps", "-a", "-q", "--filter", "label=fp-runner=1"],
                         capture_output=True, text=True).stdout
    return out.split()


@pytest.fixture(autouse=True)
def no_leftovers():
    yield
    assert leftovers() == []


HELLO = {
    "c": '#include <stdio.h>\nint main(void){puts("hello");return 0;}\n',
    "cpp": '#include <iostream>\nint main(){std::cout << "hello\\n";}\n',
    "java": 'public class Main { public static void main(String[] a){ System.out.println("hello"); } }\n',
}


@pytest.mark.parametrize("lang", ["c", "cpp", "java"])
def test_hello_world(lang):
    r = run(lang, HELLO[lang])
    assert r["status"] == "ok", r
    res = r["results"][0]
    assert res["status"] == "ok" and res["stdout"] == "hello\n" and res["exit_code"] == 0
    assert res["truncated"] is False


ECHO = {
    "c": '#include <stdio.h>\nint main(){int a,b;if(scanf("%d %d",&a,&b)==2)printf("%d\\n",a+b);return 0;}',
    "cpp": '#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b<<"\\n";}',
    "java": 'import java.util.*;\npublic class Main{public static void main(String[] x){Scanner s=new Scanner(System.in);'
            'System.out.println(s.nextInt()+s.nextInt());}}',
}


@pytest.mark.parametrize("lang", ["c", "cpp", "java"])
def test_stdin_handling_one_result_per_stdin(lang):
    r = run(lang, ECHO[lang], ["1 2\n", "10 20\n", "-5 5\n"])
    assert [x["stdout"] for x in r["results"]] == ["3\n", "30\n", "0\n"]


def test_java_utf8_roundtrip():
    src = ('import java.util.*;\npublic class Main{public static void main(String[] x){'
           'Scanner s=new Scanner(System.in);System.out.println(s.nextLine()+"\\u00e9");}}')
    r = run("java", src, ["héllo 世\n"])
    assert r["results"][0]["stdout"] == "héllo 世é\n"


@pytest.mark.parametrize("lang,src", [
    ("c", "int main( { return 0 }"),
    ("cpp", "int main() { undefined_fn(); }"),
    ("java", "public class Main { void x( }"),
])
def test_compile_error(lang, src):
    r = run(lang, src)
    assert r["status"] == "compile_error" and r["results"] == []
    assert r["compile_output"] and len(r["compile_output"]) <= 8000


def test_runtime_error_exit_code_and_signal():
    r = run("c", "int main(){return 3;}")
    assert r["results"][0]["status"] == "runtime_error" and r["results"][0]["exit_code"] == 3
    r = run("c", "#include <signal.h>\nint main(){raise(SIGSEGV);}")
    res = r["results"][0]
    assert res["status"] == "runtime_error" and res["exit_code"] == 139 and "SIGSEGV" in res["stderr"]


def test_java_exception_is_runtime_error():
    r = run("java", 'public class Main{public static void main(String[] a){throw new RuntimeException("boom");}}')
    res = r["results"][0]
    assert res["status"] == "runtime_error" and res["exit_code"] == 1 and "boom" in res["stderr"]


def test_infinite_loop_is_time_limit_and_container_removed():
    r = run("c", "int main(){for(;;);}", ["", ""], t=500)
    assert [x["status"] for x in r["results"]] == ["time_limit", "time_limit"]
    assert r["results"][0]["exit_code"] is None
    assert leftovers() == []


def test_memory_limit_c():
    src = ('#include <stdlib.h>\n#include <stdio.h>\nint main(){size_t n=(size_t)2<<30;volatile char*p=malloc(n);'
           'if(!p)return 7;long s=0;for(size_t i=0;i<n;i+=4096){p[i]=1;s+=p[i];}printf("%ld\\n",s);return 0;}')
    r = run("c", src, mem=65536)
    assert r["results"][0]["status"] == "memory_limit", r


def test_memory_limit_java_heap():
    src = ('public class Main{public static void main(String[] a){java.util.List<long[]> l=new java.util.ArrayList<>();'
           'while(true) l.add(new long[1<<20]);}}')
    r = run("java", src, mem=131072)
    assert r["results"][0]["status"] == "memory_limit", r


def test_fork_bomb_is_contained():
    src = ('#define _GNU_SOURCE\n#include <stdio.h>\n#include <unistd.h>\nint main(){int n=0;for(;;){int p=fork();'
           'if(p==0){for(;;)pause();}if(p<0)break;n++;}printf("%d\\n",n);return 0;}')
    r = run("c", src, t=3000)
    res = r["results"][0]
    assert res["status"] in ("ok", "time_limit"), r
    if res["status"] == "ok":
        assert 1 <= int(res["stdout"]) < 16
    assert leftovers() == []


NET = '''#include <stdio.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
int main(){int s=socket(AF_INET,SOCK_STREAM,0);struct sockaddr_in a={0};a.sin_family=AF_INET;a.sin_port=htons(53);
inet_pton(AF_INET,"1.1.1.1",&a.sin_addr);int r=connect(s,(struct sockaddr*)&a,sizeof a);printf("connect=%d\\n",r);return 0;}
'''


def test_network_is_unreachable():
    r = run("c", NET)
    assert r["results"][0]["stdout"] == "connect=-1\n", r


FS = '''#include <stdio.h>
#include <string.h>
int main(){char b[4096]={0};FILE*f=fopen("/etc/passwd","r");size_t n=f?fread(b,1,4095,f):0;
printf("passwd_has_root=%d lines_user=%d\\n",strstr(b,"root:")!=0,strstr(b,"ava")!=0);
FILE*w=fopen("/etc/fp_test","w");printf("etc_write=%d\\n",w!=0);
FILE*x=fopen("/usr/bin/fp_test","w");printf("usr_write=%d\\n",x!=0);
FILE*t=fopen("/tmp/fp_test","w");printf("tmp_write=%d\\n",t!=0);
FILE*k=fopen("/work/fp_test","w");printf("work_write=%d\\n",k!=0);
FILE*h=fopen("/Users","r");printf("host_users=%d\\n",h!=0);return 0;}
'''


def test_filesystem_isolation():
    r = run("c", FS)
    out = r["results"][0]["stdout"]
    assert "passwd_has_root=1" in out and "lines_user=0" in out
    assert "etc_write=0" in out and "usr_write=0" in out
    assert "work_write=1" in out and "host_users=0" in out


def test_environment_is_clean():
    src = ('#include <stdio.h>\nextern char**environ;int main(){for(char**e=environ;*e;e++)puts(*e);return 0;}')
    out = run("c", src)["results"][0]["stdout"]
    keys = {line.split("=")[0] for line in out.splitlines()}
    assert {"LC_ALL", "TZ"} <= keys
    assert not keys & {"RUNNER_TENANTS", "USER", "SSH_AUTH_SOCK", "AWS_SECRET_ACCESS_KEY"}
    assert "TZ=UTC" in out and "LC_ALL=C.UTF-8" in out


def test_huge_output_is_output_limit_and_truncated():
    src = '#include <stdio.h>\nint main(){for(;;)puts("0123456789012345678901234567890123456789");}'
    r = run("c", src, ["", "x"], t=5000)
    res = r["results"][0]
    assert res["status"] == "output_limit" and res["truncated"] is True
    assert len(res["stdout"].encode()) == 256 * 1024
    assert r["results"][1]["status"] == "output_limit"
    assert leftovers() == []


def test_stderr_is_capped():
    src = ('#include <stdio.h>\nint main(){for(int i=0;i<4000;i++)fprintf(stderr,"%0100d\\n",i);puts("done");return 0;}')
    res = run("c", src)["results"][0]
    assert res["truncated"] is True and len(res["stderr"].encode()) <= 64 * 1024 and res["stdout"] == "done\n"


@pytest.mark.parametrize("src,expect", [
    ("public class Main{public static void main(String[] a){System.out.println(1);}}", "1\n"),
    ("public class Solution{public static void main(String[] a){System.out.println(2);}}", "2\n"),
    ("class Helper{}\npublic class Foo{public static void main(String[] a){System.out.println(3);}}", "3\n"),
    ("class Tester{public static void main(String[] a){System.out.println(4);}}\nclass Other{}", "4\n"),
    ("import java.util.*;\n// public class Wrong{}\nclass Main{public static void main(String[] a){System.out.println(5);}}",
     "5\n"),
])
def test_java_class_naming(src, expect):
    r = run("java", src)
    assert r["status"] == "ok" and r["results"][0]["stdout"] == expect, r


@pytest.mark.parametrize("src", [
    "package x;\npublic class Main{public static void main(String[] a){}}",
    "int x;",
])
def test_java_rejected_without_container(src):
    r = run("java", src)
    assert r["status"] == "compile_error" and "public class Main" in r["compile_output"]


def test_sweep_removes_only_old_prefixed_labelled_containers():
    ex = make_exec()
    base = ["docker", "run", "-d", "--rm", "--pull", "never", "--network", "none"]
    mine = "fpr-" + "a" * 16
    foreign = "other-fp-test-container"
    unlabelled = "fpr-" + "b" * 16
    subprocess.run(base + ["--name", mine, "--label", "fp-runner=1", "fp-runner-runtime:1", "sleep", "60"],
                   check=True, capture_output=True)
    subprocess.run(base + ["--name", foreign, "fp-runner-runtime:1", "sleep", "60"], check=True, capture_output=True)
    subprocess.run(base + ["--name", unlabelled, "fp-runner-runtime:1", "sleep", "60"], check=True, capture_output=True)
    try:
        assert asyncio.run(ex.sweep()) == 0
        import app.executor as E
        old = E.SWEEP_AGE_S
        E.SWEEP_AGE_S = -1
        try:
            assert asyncio.run(ex.sweep()) == 1
        finally:
            E.SWEEP_AGE_S = old
        names = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True,
                               text=True).stdout.split()
        assert mine not in names and foreign in names and unlabelled in names
    finally:
        subprocess.run(["docker", "rm", "-f", mine, foreign, unlabelled], capture_output=True)


def test_startup_probe_and_signed_end_to_end():
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import SECRET, Signed

    s = Settings(tenants={"t1": SECRET}, warm_pool=1, compile_timeout_s=COMPILE_TIMEOUT, java_startup_ms=JAVA_STARTUP)
    ex = DockerExecutor(s)
    with TestClient(create_app(s, ex)) as client:
        h = client.get("/v1/health").json()
        assert h["languages"] == ["c", "cpp", "java"]
        sg = Signed(client)
        langs = sg.get("/v1/languages").json()["languages"]
        assert {x["key"] for x in langs} == {"c", "cpp", "java"} and "14" in langs[0]["label"]
        r = sg.post({"language": "c", "source": HELLO["c"], "stdins": ["", ""], "time_limit_ms": SLOW}).json()
        assert r["status"] == "ok" and r["runtime_version"].startswith("gcc 14")
        assert [x["stdout"] for x in r["results"]] == ["hello\n", "hello\n"]
    assert leftovers() == []
