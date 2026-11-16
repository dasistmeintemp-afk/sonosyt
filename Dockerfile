FROM python:3.12-slim

# yt-dlp needs a JavaScript runtime to read YouTube's player code reliably.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ARG VERSION=0.1.0
ENV PYTHONUNBUFFERED=1 SONOSYT_VERSION=${VERSION}
LABEL org.opencontainers.image.title="sonosyt" \
      org.opencontainers.image.description="YouTube-Ton auf Sonos-Lautsprechern" \
      org.opencontainers.image.source="https://github.com/dasistmeintemp-afk/sonosyt" \
      org.opencontainers.image.version="${VERSION}"
WORKDIR /app
COPY pyproject.toml README.md ./
COPY sonosyt ./sonosyt
RUN pip install --no-cache-dir .

VOLUME /cache
EXPOSE 8327
ENTRYPOINT ["sonosyt", "serve", "--cache-dir", "/cache", "--auto-update"]
