FROM golang:1.22-bookworm AS build

WORKDIR /src
COPY backend/go.mod backend/go.sum ./backend/
RUN cd backend && go mod download
COPY backend ./backend
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/volterra-api ./backend/cmd/api

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && curl -fsSL -o /tmp/duckdb.zip https://github.com/duckdb/duckdb/releases/latest/download/duckdb_cli-linux-amd64.zip \
    && unzip -q /tmp/duckdb.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/duckdb \
    && rm -rf /var/lib/apt/lists/* /tmp/duckdb.zip

WORKDIR /app
COPY --from=build /out/volterra-api ./volterra-api
COPY warehouse ./warehouse
COPY ml ./ml

ENV PORT=8080
ENV VOLTERRA_WAREHOUSE_PATH=/data/volterra.duckdb
ENV VOLTERRA_QUEUE_RISK_PREDICTIONS_PATH=/data/queue_risk_predictions.json
EXPOSE 8080
CMD ["./volterra-api"]
