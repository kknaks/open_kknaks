#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_TOOLS_DIR="${SCRIPT_DIR}/.claude-tools"
CODEX_TOOLS_DIR="${SCRIPT_DIR}/.codex-tools"
CODEX_HOME_DIR="${SCRIPT_DIR}/.codex-home"
NODE_VERSION="22.16.0"

# ──────────────────────────────────────────────
# 1. Claude OAuth 토큰 입력
# ──────────────────────────────────────────────
echo "Claude Code OAuth 토큰을 입력하세요"
echo "(terminal: claude setup-token)"
echo "주의: Anthropic Console API key가 아니라 Claude Code setup token을 입력해야 합니다."
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
# 4. Codex CLI 설치/인증 디렉토리 준비
# ──────────────────────────────────────────────
echo ""
echo "=== Codex CLI 설치 (Docker/Linux용) ==="

if command -v docker &> /dev/null; then
    mkdir -p "${CODEX_TOOLS_DIR}"
    docker run --rm \
        -v "${CODEX_TOOLS_DIR}:/codex-tools" \
        "node:${NODE_VERSION}-bookworm-slim" \
        npm install --prefix /codex-tools @openai/codex
    echo "Codex CLI 설치 완료: .codex-tools/"
else
    echo "WARN: docker 명령을 찾을 수 없어 Codex CLI 설치를 건너뜁니다."
    echo "      Docker worker에서 codex provider를 쓰려면 .codex-tools를 따로 준비해야 합니다."
fi

if [ -d "${HOME}/.codex" ]; then
    mkdir -p "${CODEX_HOME_DIR}"
    cp -R "${HOME}/.codex/." "${CODEX_HOME_DIR}/"
    echo "Codex 인증/config 복사 완료: .codex-home/"
else
    mkdir -p "${CODEX_HOME_DIR}"
    echo "WARN: ${HOME}/.codex 디렉토리가 없어 빈 .codex-home을 생성했습니다."
    echo "      Docker worker에서 codex provider를 쓰려면 Codex 인증/config를 .codex-home에 준비해야 합니다."
fi

# ──────────────────────────────────────────────
# 5. 모드 선택
# ──────────────────────────────────────────────

echo ""
echo "=== 설치 완료 ==="
echo ""
echo "  .claude-tools/node/         — Linux Node.js (컨테이너용)"
echo "  .claude-tools/node_modules/ — Claude Code CLI (JS)"
echo "  .codex-tools/               — Codex CLI (컨테이너용)"
echo "  .codex-home/                — Codex auth/config (컨테이너용)"
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
        # 5-1. 예시 프로젝트: .env 생성 + Docker Compose 실행
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
        # 5-2. 내 프로젝트 연결: 설정 가이드 출력
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
      - ./.codex-tools:/codex-tools:ro
      - ./.codex-home:/codex-home
    environment:
      - CLAUDE_CODE_OAUTH_TOKEN=\${CLAUDE_CODE_OAUTH_TOKEN}
      - WORK_DIR=/project
      - CODEX_HOME=/codex-home
      - PATH=/codex-tools/node_modules/.bin:/claude-tools/node/bin:/claude-tools/node_modules/.bin:/usr/local/bin:/usr/bin:/bin
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
