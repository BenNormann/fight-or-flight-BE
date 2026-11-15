FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FLASK_APP=wsgi:app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "wsgi:app"]

# ---- tippecanoe build (only used by the `tools` target) ----
FROM debian:bookworm-slim AS tippecanoe-build
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git ca-certificates libsqlite3-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/felt/tippecanoe.git /src \
    && make -C /src -j"$(nproc)" && make -C /src install

FROM app AS tools
RUN apt-get update && apt-get install -y --no-install-recommends libsqlite3-0 zlib1g osmium-tool curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=tippecanoe-build /usr/local/bin/tippecanoe* /usr/local/bin/
COPY --from=tippecanoe-build /usr/local/bin/tile-join /usr/local/bin/
