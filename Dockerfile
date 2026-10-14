FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY flashcrash ./flashcrash
RUN uv sync --frozen --no-dev
ENV PORT=8000 HOST=0.0.0.0
EXPOSE 8000
CMD ["/app/.venv/bin/flash-crash"]
