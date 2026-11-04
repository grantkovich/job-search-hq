# Job Search HQ app image. Builds for amd64 and arm64.
#
# - Dependencies install from requirements.lock with hashes (--require-hashes), so a
#   tampered package fails the build.
# - Runs as an unprivileged user; compose.yaml adds a read-only root filesystem,
#   no capabilities and no-new-privileges.
# - Pin the base image by digest in production: Dependabot keeps it current (.github/dependabot.yml).
FROM python:3.15-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.lock ./
RUN python -m venv /venv && /venv/bin/pip install --require-hashes --no-deps -r requirements.lock

FROM python:3.15-slim-bookworm
ARG VOICE=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tini $( [ "$VOICE" = "1" ] && echo ffmpeg ) \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --user-group --home-dir /data --no-create-home --shell /usr/sbin/nologin jobhq \
 && mkdir -p /data && chown jobhq:jobhq /data
COPY --from=build /venv /venv
COPY src /app/src
ENV PATH=/venv/bin:$PATH PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 JOBHQ_DATA_DIR=/data \
    HF_HOME=/data/models HOME=/data/work/home
USER jobhq
WORKDIR /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "jobhq.cli", "serve"]
