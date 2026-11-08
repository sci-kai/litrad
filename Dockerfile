# litresearch in server mode: the web UI on port 8080, all data in the volume /data.
#
#   docker build -t litresearch .
#   docker run --rm -it -v litresearch:/data litresearch litresearch set-password   # once
#   docker run -d --name litresearch -p 8080:8080 -v litresearch:/data litresearch

# --- stage 1: build the wheel -------------------------------------------------------
FROM python:3.13-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m build --wheel --outdir /dist

# --- stage 2: runtime (no compilers; all dependencies come as binary wheels) --------
FROM python:3.13-slim
LABEL org.opencontainers.image.title="litresearch" \
      org.opencontainers.image.description="Personal literature recommender (server mode)" \
      org.opencontainers.image.licenses="LicenseRef-MIT-Commons-Clause"

RUN useradd --create-home --uid 10001 litresearch \
    && mkdir /data && chown litresearch:litresearch /data
COPY --from=build /dist/*.whl /tmp/dist/
RUN pip install --no-cache-dir /tmp/dist/*.whl && rm -rf /tmp/dist

# the first command creates /data/config.yaml with all data next to it
ENV LITRESEARCH_CONFIG=/data/config.yaml \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER litresearch
VOLUME /data
EXPOSE 8080

# /login is open without a password too (it is the only page that needs no session)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/login', timeout=4)"]

# serving to the network needs a password: set it once with `litresearch set-password`
CMD ["litresearch", "serve", "--no-choose", "--network", "all", "--port", "8080"]
