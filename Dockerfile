# Poetry-based Docker image for the Telegram bot
FROM python:3.12-slim AS base


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.8.3 \
    TZ=Europe/Rome


RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata curl \
    && rm -rf /var/lib/apt/lists/*


# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 - \
    && ln -s /root/.local/bin/poetry /usr/local/bin/poetry \
    && poetry --version


WORKDIR /app


# Copy project metadata first (better layer caching)
COPY pyproject.toml /app/pyproject.toml
# If you have a poetry.lock, copy it too for reproducible builds
# COPY poetry.lock /app/poetry.lock


# Configure Poetry to install into the system site-packages (no venv in container)
RUN poetry config virtualenvs.create false && poetry install --only main --no-interaction --no-ansi


# Copy source code
COPY app /app/app


# Create non-root user and data dir
RUN useradd -ms /bin/bash bot && mkdir -p /app/data && chown -R bot:bot /app
USER bot


HEALTHCHECK --interval=60s --timeout=5s --retries=3 CMD python -c "import json; print('ok')" || exit 1


CMD ["python", "-m", "app.bot.__main__"]