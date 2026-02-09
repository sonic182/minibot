FROM python:3.12-slim

ARG POETRY_VERSION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends firejail curl \
    && rm -rf /var/lib/apt/lists/*

RUN if [ -n "$POETRY_VERSION" ]; then pip install --no-cache-dir "poetry==$POETRY_VERSION"; else pip install --no-cache-dir poetry; fi

WORKDIR /app

COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-ansi --no-root

COPY . .
RUN poetry install --no-ansi --only-root

CMD ["minibot"]
