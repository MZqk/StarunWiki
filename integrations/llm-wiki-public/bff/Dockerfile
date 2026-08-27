FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/public-bff .
RUN mkdir -p /out/data

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/public-bff /public-bff
COPY --chown=nonroot:nonroot --from=build /out/data /data/public-bff
EXPOSE 8091
USER nonroot:nonroot
ENTRYPOINT ["/public-bff"]
