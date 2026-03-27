#!/bin/bash
set -e

echo "Claude Code OAuth 토큰을 입력하세요"
echo "(web: https://console.anthropic.com/settings/keys)"
echo "(terminal: claude setup-token)"
echo ""
read -rp "Token: " TOKEN

if [ -z "$TOKEN" ]; then
    echo "ERROR: 토큰이 비어있습니다."
    exit 1
fi

cat > .env << EOF
REDIS_URL=redis://redis:6379
NAMESPACE=example
QUEUES=default,analysis,review
CONCURRENCY=2
WORK_DIR=/project
CLAUDE_CODE_OAUTH_TOKEN=${TOKEN}
EOF

echo ""
echo "=== .env 생성 완료 ==="
echo ""
echo "Docker Compose 빌드 + 실행 중..."
docker compose up -d --build

echo ""
echo "=== 완료 ==="
echo "http://localhost:8000"
