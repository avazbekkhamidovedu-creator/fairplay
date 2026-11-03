"""Loopback-only test runner. Never import into production server startup."""
import argparse
import json
import os
import socket
from pathlib import Path
import uvicorn
from admission_fixture import create_app


def main():
    parser = argparse.ArgumentParser(description="Run simulated FairPlay admission on 127.0.0.1 only")
    parser.add_argument("--ready-file", type=Path, help="Write only selected host/port, never credentials")
    args = parser.parse_args()
    native = os.environ.get("FAIRPLAY_FIXTURE_NATIVE_KEY", "")
    student = os.environ.get("FAIRPLAY_FIXTURE_STUDENT_KEY", "")
    if not native or not student or native == student:
        parser.error("Set distinct throwaway FAIRPLAY_FIXTURE_NATIVE_KEY and FAIRPLAY_FIXTURE_STUDENT_KEY")
    try:
        port = int(os.environ.get("FAIRPLAY_FIXTURE_PORT", "8765"))
        if not 0 <= port <= 65535:
            raise ValueError()
    except ValueError:
        parser.error("FAIRPLAY_FIXTURE_PORT must be 0..65535; 0 selects an available test port")
    # No configurable host: this runner cannot expose the fake service on a LAN.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    if args.ready_file:
        args.ready_file.write_text(json.dumps({"host": "127.0.0.1", "port": sock.getsockname()[1]}))
    app = create_app(native_key=native, student_key=student)
    config = uvicorn.Config(app, host="127.0.0.1", port=sock.getsockname()[1],
                            log_level="warning", access_log=False)
    try:
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        sock.close()


if __name__ == "__main__":
    main()
