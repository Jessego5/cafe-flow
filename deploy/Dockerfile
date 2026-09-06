# One container serves the API and the three built views, so there is no CORS
# configuration to get wrong and no second origin to keep in step.
#
# Build from the repository root:
#   docker build -f deploy/Dockerfile -t cafe-flow .

# ---- stage 1: the three frontends ----------------------------------------
FROM node:22-alpine AS web

WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build


# ---- stage 2: litestream --------------------------------------------------
FROM alpine:3.20 AS litestream

ARG LITESTREAM_VERSION=0.3.13
RUN apk add --no-cache curl && \
    case "$(uname -m)" in \
      x86_64)  ARCH=amd64 ;; \
      aarch64) ARCH=arm64 ;; \
      *) echo "unsupported architecture $(uname -m)" >&2; exit 1 ;; \
    esac && \
    curl -fsSL "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-linux-${ARCH}.tar.gz" \
      | tar -xz -C /usr/local/bin litestream


# ---- stage 3: the app -----------------------------------------------------
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    PORT=8080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ./core/
COPY app/ ./app/
# baked in as the first-boot seed only; the running config is read from the
# volume, so M8 can drop in observed.yaml without a rebuild
COPY params/ ./params/
COPY --from=web /web/dist ./web/dist
COPY --from=litestream /usr/local/bin/litestream /usr/local/bin/litestream
COPY deploy/litestream.yml /etc/litestream.yml
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY deploy/verify-restore.sh /usr/local/bin/verify-restore.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/verify-restore.sh

# Set here rather than only exported by the entrypoint, so a shell that never
# ran it sees the same database. `fly ssh console -C "python -m app.create_staff
# jess"` used to make the account in the image's default path and report
# success, leaving somebody locked out of an app that had just told them the
# account existed. The entrypoint still honours an override.
ENV DATA_DIR=/data \
    CAFE_DB=/data/cafe.db

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
