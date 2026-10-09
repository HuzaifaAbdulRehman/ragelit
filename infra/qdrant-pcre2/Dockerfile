FROM qdrant/qdrant:v1.19.2@sha256:b7b0444c4c351c970b98e90a6f89c2ee4287c65b44e52b4cb503fa5b2aa927ad

RUN apt-get update -o Acquire::Languages=none \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        --only-upgrade 'libpcre2-8-0=10.46-1~deb13u3' \
    && rm -rf /var/lib/apt/lists/*
