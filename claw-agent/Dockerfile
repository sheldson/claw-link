FROM python:3.12-slim
WORKDIR /app

# Only install base dependencies — ClawLink is NOT installed
# The agent installs it when the owner tells it to
RUN pip install --no-cache-dir openai>=1.0.0 httpx>=0.27.0 aiohttp>=3.9.0

COPY agent.py .

CMD ["python", "agent.py"]
