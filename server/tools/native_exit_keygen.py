import base64
import os
import sys

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: native_exit_keygen.py PRIVATE_KEY_FILE", file=sys.stderr)
        return 2
    path = argv[1]
    priv = X25519PrivateKey.generate()
    private_b64 = base64.b64encode(priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode("ascii")
    public_b64 = base64.b64encode(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"{path} already exists, not overwriting", file=sys.stderr)
        return 1
    with os.fdopen(fd, "w") as f:
        f.write(private_b64 + "\n")
    os.chmod(path, 0o600)
    print(public_b64)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
