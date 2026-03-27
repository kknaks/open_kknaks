#!/bin/bash
set -e

# Claude CLI 바이너리 찾기
CLAUDE_BIN=$(which claude 2>/dev/null || true)
if [ -z "$CLAUDE_BIN" ]; then
    echo "ERROR: claude CLI를 찾을 수 없습니다."
    echo "  설치: https://claude.ai/download"
    echo "  설치 후: claude login"
    exit 1
fi

# Node.js prefix 찾기
CLAUDE_DIR=$(cd "$(dirname "$CLAUDE_BIN")/.." && pwd)

# 로그인 상태 확인
if ! claude auth status &>/dev/null; then
    echo "WARNING: Claude Code 로그인이 필요합니다."
    echo "  실행: claude login"
fi

# .env 생성
cat > .env << EOF
CLAUDE_DIR=${CLAUDE_DIR}
EOF

echo "=== setup 완료 ==="
echo "Claude CLI: ${CLAUDE_BIN}"
echo "마운트 경로: ${CLAUDE_DIR} → /host-node"
echo ""
echo "실행: docker compose up -d"
