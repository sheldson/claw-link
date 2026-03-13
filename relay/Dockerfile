FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY relay/ relay/

RUN pip install --no-cache-dir .

# SQLite data directory
RUN mkdir -p /data
ENV DATABASE_URL=sqlite+aiosqlite:////data/claw-link.db
ENV HOST=0.0.0.0
ENV PORT=8080

EXPOSE 8080

CMD ["uvicorn", "relay.main:app", "--host", "0.0.0.0", "--port", "8080"]
