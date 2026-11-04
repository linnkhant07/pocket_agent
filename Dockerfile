FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY examples ./examples
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:${PATH}" \
    POCKET_AGENT_HOME=/data
VOLUME ["/data"]
ENTRYPOINT ["pocket-agent"]
CMD ["hardware"]
