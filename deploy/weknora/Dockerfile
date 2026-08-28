# syntax=docker/dockerfile:1.7
FROM golang:1.26-bookworm AS builder

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git libsqlite3-dev patch ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=weknora-source / /app
COPY weknora-v0.7.2-sqlite-wiki.patch /tmp/weknora-sqlite.patch

RUN echo "f04637521f48d57408f1664d3686b650c55103469e9c48dce5156fe9517b8ebd  internal/application/repository/wiki_page.go" | sha256sum -c - && \
    echo "b872b0315c222da186a88fc990fab132eb5bc859bb858816cfa0ac4848f71cf5  internal/agent/tools/wiki_tools.go" | sha256sum -c - && \
    echo "332d1b324247c1d48ac9dac24f5a30d09716600701a92e46d71dfb8b6829a499  internal/application/repository/wiki_page_test.go" | sha256sum -c - && \
    echo "1810a2e8f37a28bd831607da5049d29508c2efd07a1c4940cc7600647dd88d16  internal/agent/tools/wiki_tools_test.go" | sha256sum -c - && \
    patch -p1 < /tmp/weknora-sqlite.patch

RUN --mount=type=cache,target=/go/pkg/mod go mod download
RUN gofmt -w internal/application/repository/wiki_page.go internal/application/repository/wiki_page_test.go internal/agent/tools/wiki_tools.go internal/agent/tools/wiki_tools_test.go
RUN --mount=type=cache,target=/go/pkg/mod go test ./internal/application/repository ./internal/agent/tools
RUN go run cmd/download/duckdb/duckdb.go
ENV VERSION=v0.7.2-sqlite-wiki
ENV COMMIT_ID=3d5d8bfcdfeeea266b292b71cea616847af28d0f
ENV BUILD_TIME=derived
ENV GO_VERSION=1.26
RUN --mount=type=cache,target=/go/pkg/mod make build-prod

FROM wechatopenai/weknora-app:v0.7.2
RUN mkdir -p /data/weknora && chown appuser:appuser /data/weknora
COPY --chown=appuser:appuser --from=builder /app/WeKnora /app/WeKnora
