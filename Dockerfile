# litrad (the Literature Radar Game) in server mode: web UI on port 8080, data in /data.
#
#   docker build -t litrad .
#   docker run --rm -it -v litrad:/data litrad litrad set-password   # once
#   docker run -d --name litrad -p 8080:8080 -v litrad:/data litrad

# --- stage 1: build the wheel -------------------------------------------------------
FROM python:3.13-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m build --wheel --outdir /dist

# --- stage 2: runtime (no compilers; all dependencies come as binary wheels) --------
FROM python:3.13-slim
LABEL org.opencontainers.image.title="litrad" \
      org.opencontainers.image.description="litrad, the Literature Radar Game (server mode)" \
      org.opencontainers.image.licenses="LicenseRef-MIT-Commons-Clause"

RUN useradd --create-home --uid 10001 litrad \
    && mkdir /data && chown litrad:litrad /data
COPY --from=build /dist/*.whl /tmp/dist/
RUN pip install --no-cache-dir /tmp/dist/*.whl && rm -rf /tmp/dist

# the first command creates /data/config.yaml with all data next to it
ENV LITRAD_CONFIG=/data/config.yaml \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER litrad
VOLUME /data
EXPOSE 8080

# /login is open without a password too (it is the only page that needs no session)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/login', timeout=4)"]

# serving to the network needs a password: set it once with `litrad set-password`
CMD ["litrad", "serve", "--no-choose", "--network", "all", "--port", "8080"]
