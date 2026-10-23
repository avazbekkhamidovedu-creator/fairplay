FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
# Audit S-17: never run as root. data/ (SQLite, uploads, scans) belongs to this user.
RUN useradd --system --uid 10001 --home /app fairplay && mkdir -p /app/data && chown -R fairplay /app/data
USER fairplay
EXPOSE 8000
# One process per core (WEB_CONCURRENCY = the container's CPUs); several containers behind
# nginx / a load balancer for more (server/NOTES.md "Sizing").
# perf 0.6.2: --timeout-keep-alive 75 is longer than nginx's upstream keepalive_timeout (60 s),
# so the proxy always closes idle connections first (no 502 on a reused, just-closed one);
# --backlog for the exam-start burst.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-4} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\" --no-access-log --timeout-keep-alive 75 --backlog 4096"]
