FROM debian:trixie-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc g++ libc6-dev openjdk-21-jdk-headless \
 && rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man \
 && java -Xshare:dump >/dev/null 2>&1 || true

USER 65534:65534
WORKDIR /work
