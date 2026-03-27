#!/bin/bash
set -e

CLAUDE_TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)/.claude-tools"
NODE_VERSION="22.16.0"

# ──────────────────────────────────────────────
# 1. Claude OAuth 토큰 입력
# ──────────────────────────────────────────────
echo "Claude Code OAuth 토큰을 입력하세요"
echo "(web: https://console.anthropic.com/settings/keys)"
echo "(terminal: claude setup-token)"
echo ""
read -rp "Token: " TOKEN

if [ -z "$TOKEN" ]; then
    echo "ERROR: 토큰이 비어있습니다."
    exit 1
fi

# ──────────────────────────────────────────────
# 2. Linux용 Node.js 바이너리 다운로드
# ──────────────────────────────────────────────
echo ""
echo "=== Linux용 Node.js ${NODE_VERSION} 다운로드 ==="

# Docker 플랫폼에 맞는 아키텍처 결정
# macOS arm64 → Docker는 linux/arm64, macOS x86 → linux/x64
ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  NODE_ARCH="x64" ;;
    aarch64) NODE_ARCH="arm64" ;;
    arm64)   NODE_ARCH="arm64" ;;
    *)       echo "ERROR: 지원하지 않는 아키텍처: $ARCH"; exit 1 ;;
esac

NODE_DIR="${CLAUDE_TOOLS_DIR}/node"
NODE_TARBALL="node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/${NODE_TARBALL}"

if [ -x "${NODE_DIR}/bin/node" ]; then
    INSTALLED_VER=$("${NODE_DIR}/bin/node" --version 2>/dev/null || echo "")
    if [ "$INSTALLED_VER" = "v${NODE_VERSION}" ]; then
        echo "Node.js v${NODE_VERSION} 이미 설치됨 — 건너뜀"
    else
        rm -rf "${NODE_DIR}"
    fi
fi

if [ ! -x "${NODE_DIR}/bin/node" ]; then
    mkdir -p "${CLAUDE_TOOLS_DIR}"
    echo "다운로드: ${NODE_URL}"
    curl -fSL "${NODE_URL}" -o "${CLAUDE_TOOLS_DIR}/${NODE_TARBALL}"
    mkdir -p "${NODE_DIR}"
    tar -xJf "${CLAUDE_TOOLS_DIR}/${NODE_TARBALL}" -C "${NODE_DIR}" --strip-components=1
    rm -f "${CLAUDE_TOOLS_DIR}/${NODE_TARBALL}"
    echo "Node.js ${NODE_VERSION} (linux/${NODE_ARCH}) 설치 완료"
fi

# ──────────────────────────────────────────────
# 3. Claude Code CLI 설치 (npm)
# ──────────────────────────────────────────────
echo ""
echo "=== Claude Code CLI 설치 ==="

# 호스트 npm 사용 (JS 파일은 플랫폼 무관)
if ! command -v npm &> /dev/null; then
    echo "ERROR: npm이 설치되어 있지 않습니다."
    echo "Node.js를 먼저 설치하세요: https://nodejs.org/"
    exit 1
fi

cd "${CLAUDE_TOOLS_DIR}"
if [ ! -d "node_modules/@anthropic-ai/claude-code" ]; then
    npm install --prefix "${CLAUDE_TOOLS_DIR}" @anthropic-ai/claude-code
    echo "Claude Code CLI 설치 완료"
else
    echo "Claude Code CLI 이미 설치됨 — 업데이트 확인"
    npm update --prefix "${CLAUDE_TOOLS_DIR}" @anthropic-ai/claude-code
fi

# ──────────────────────────────────────────────
# 4. 모드 선택
# ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "=== 설치 완료 ==="
echo ""
echo "  .claude-tools/node/         — Linux Node.js (컨테이너용)"
echo "  .claude-tools/node_modules/ — Claude Code CLI (JS)"
echo ""
echo "어떻게 사용하시겠습니까?"
echo ""
echo "  1) 예시 프로젝트 실행 (Docker Compose → Redis + Worker + Web UI)"
echo "  2) 내 프로젝트에 연결 (설정 가이드 출력)"
echo ""
read -rp "선택 [1/2]: " MODE

case "$MODE" in
    1)
        # ──────────────────────────────────────────────
        # 4-1. 예시 프로젝트: .env 생성 + Docker Compose 실행
        # ──────────────────────────────────────────────
        cat > "${SCRIPT_DIR}/.env" << EOF
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
        cd "${SCRIPT_DIR}"
        docker compose up -d --build

        echo ""
        echo "=== 완료 ==="
        echo "  Web UI:  http://localhost:8000"
        echo "  Swagger: http://localhost:8000/docs"
        echo "  Redis:   localhost:6379"
        ;;

    2)
        # ──────────────────────────────────────────────
        # 4-2. 내 프로젝트 연결: 설정 가이드 출력
        # ──────────────────────────────────────────────
        echo ""
        read -rp "프로젝트 절대 경로: " PROJECT_DIR

        if [ -z "$PROJECT_DIR" ]; then
            echo "ERROR: 경로가 비어있습니다."
            exit 1
        fi
        if [ ! -d "$PROJECT_DIR" ]; then
            echo "ERROR: 디렉토리가 존재하지 않습니다: $PROJECT_DIR"
            exit 1
        fi

        cat > "${SCRIPT_DIR}/docker-compose.override.yml" << EOF
services:
  worker:
    volumes:
      - ${PROJECT_DIR}:/project:ro
      - ./.claude-tools:/claude-tools:ro
    environment:
      - CLAUDE_CODE_OAUTH_TOKEN=\${CLAUDE_CODE_OAUTH_TOKEN}
      - WORK_DIR=/project
EOF

        echo ""
        echo "=== docker-compose.override.yml 생성 완료 ==="
        echo "  프로젝트: ${PROJECT_DIR} → /project"
        echo ""
        echo "토큰을 환경변수로 설정하세요:"
        echo ""
        echo "  export CLAUDE_CODE_OAUTH_TOKEN=${TOKEN}"
        echo ""
        echo "또는 .env에 추가:"
        echo ""
        echo "  CLAUDE_CODE_OAUTH_TOKEN=${TOKEN}"
        echo ""
        echo "실행:"
        echo "  cd ${SCRIPT_DIR}"
        echo "  docker compose up -d --build"
        ;;

    *)
        echo "ERROR: 1 또는 2를 선택하세요."
        exit 1
        ;;
esac
