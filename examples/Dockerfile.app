FROM python:3.12-slim

WORKDIR /lib
COPY open_kknaks ./open_kknaks
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[redis]" fastapi uvicorn jinja2 sse-starlette

WORKDIR /app
COPY examples/app/ .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
