FROM python:3.12-slim

# PyPI 패키지 설치 (소스 복사 없음)
RUN pip install --no-cache-dir "open-kknaks>=0.0.8" \
    fastapi uvicorn[standard] jinja2 sse-starlette

# 앱 코드
WORKDIR /app
COPY app/ .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
