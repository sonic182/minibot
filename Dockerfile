FROM python:3.12-slim

ARG POETRY_VERSION
ARG NODE_VERSION=24.14.0
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    XDG_CACHE_HOME=/app/data/.cache \
    HF_HOME=/app/data/.cache/huggingface \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# System deps — rarely changes
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Node.js — invalidates on NODE_VERSION bump
RUN arch="${TARGETARCH:-amd64}" \
    && case "${arch}" in \
        amd64) node_arch="x64" ;; \
        arm64) node_arch="arm64" ;; \
        *) echo "Unsupported TARGETARCH: ${arch}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" -o /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm -f /tmp/node.tar.xz \
    && node --version \
    && npm --version

# npm/playwright — independent of Python; keep before Python layers
RUN npm install -g playwright @playwright/mcp \
    && playwright install --with-deps chromium

# Poetry
RUN if [ -n "$POETRY_VERSION" ]; then pip install --no-cache-dir "poetry==$POETRY_VERSION"; else pip install --no-cache-dir poetry; fi

WORKDIR /app

# Python deps — invalidates on pyproject.toml / poetry.lock change
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-ansi --all-extras --no-root

COPY docker-requirements.txt ./
RUN pip install --no-cache-dir -r docker-requirements.txt

# Static infra — only rebuilds if UIDs or paths change
RUN groupadd --gid 1000 minibot \
    && useradd --uid 1000 --gid 1000 --create-home minibot \
    && mkdir -p /app/data /app/logs /ms-playwright \
    && chown -R 1000:1000 /app /ms-playwright

# App code — invalidates on every source change
COPY . .
RUN poetry install --no-ansi --only-root

USER 1000:1000

CMD ["minibot"]
