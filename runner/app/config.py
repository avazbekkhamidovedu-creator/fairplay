import os
from dataclasses import dataclass, field

MIN_SECRET_LEN = 32


class ConfigError(RuntimeError):
    pass


def parse_tenants(raw: str) -> dict[str, str]:
    tenants: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, secret = part.partition(":")
        if not sep or not name or not name.replace("-", "").replace("_", "").isalnum():
            raise ConfigError("RUNNER_TENANTS entries must look like tenant:secret")
        if len(secret) < MIN_SECRET_LEN:
            raise ConfigError(f"secret for tenant '{name}' is shorter than {MIN_SECRET_LEN} characters")
        tenants[name] = secret
    if not tenants:
        raise ConfigError("RUNNER_TENANTS is empty")
    return tenants


def _int(env: dict, key: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be an integer") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be between {lo} and {hi}")
    return value


@dataclass
class Settings:
    tenants: dict[str, str] = field(default_factory=dict)
    max_concurrent: int = 4
    tenant_concurrent: int = 4
    queue_wait_s: int = 30
    image: str = "fp-runner-runtime:1"
    docker_bin: str = "docker"
    java_startup_ms: int = 400
    warm_pool: int = 2
    compile_timeout_s: int = 10

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        env = dict(os.environ) if env is None else env
        return cls(
            tenants=parse_tenants(env.get("RUNNER_TENANTS", "")),
            max_concurrent=_int(env, "RUNNER_MAX_CONCURRENT", 4, 1, 256),
            tenant_concurrent=_int(env, "RUNNER_TENANT_CONCURRENT", 4, 1, 256),
            queue_wait_s=_int(env, "RUNNER_QUEUE_WAIT_S", 30, 1, 300),
            image=env.get("RUNNER_IMAGE", "fp-runner-runtime:1"),
            docker_bin=env.get("RUNNER_DOCKER_BIN", "docker"),
            java_startup_ms=_int(env, "RUNNER_JAVA_STARTUP_MS", 400, 0, 5000),
            warm_pool=_int(env, "RUNNER_WARM_POOL", 2, 0, 32),
            compile_timeout_s=_int(env, "RUNNER_COMPILE_TIMEOUT_S", 10, 1, 60),
        )
