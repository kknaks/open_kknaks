# open_kknaks — 문서 인덱스

## 설계 문서

| 문서 | 내용 | 변경 시점 |
|---|---|---|
| [ARCHITECTURE_V2.md](ARCHITECTURE_V2.md) | 상세 기술 설계 — 컴포넌트 시그니처, 데이터 흐름, 코드 예시 | 인터페이스/구조 변경 시 |
| [PRD.md](PRD.md) | 제품 요구사항 — API 인터페이스, 모델 필드, CLI 매핑 | 기능 요구사항 변경 시 |
| [PLAN.md](PLAN.md) | 구현 계획 — 스프린트, 의존 레이어, 설계 결정, 의존성 버전 | 작업 진행/완료 시 |
| [CLAUDE_CODE_ANALYSIS.md](CLAUDE_CODE_ANALYSIS.md) | Claude Code CLI 조사/분석 근거 | CLI 동작 변경 확인 시 |
| [DRAMATIQ_ANALYSIS.md](DRAMATIQ_ANALYSIS.md) | Dramatiq 구조 분석 → 차용/버린 패턴 | 참고용 (수정 거의 없음) |
| [TEST_APP.md](TEST_APP.md) | 예시 프로젝트 (Docker compose) | examples/ 변경 시 |

## 스프린트 문서

### Sprint 1 — PTY Executor + 코어 (Layer 0-2)

| 문서 | 내용 |
|---|---|
| [sprint/S1-L0.md](sprint/S1-L0.md) | Layer 0 구현 기록 (task, config, exceptions, line_buffer) |
| [sprint/S1-L1.md](sprint/S1-L1.md) | Layer 1 구현 기록 (pty_process, stream_parser, broker/base) |
| [sprint/S1-L2.md](sprint/S1-L2.md) | Layer 2 구현 기록 (executor, redis broker, lua) |
| [sprint/S1-review.md](sprint/S1-review.md) | Sprint 1 회고 |

### Sprint 2 — 미들웨어 + 배치 + DLQ (Layer 3)

| 문서 | 내용 |
|---|---|
| [sprint/S2-L3.md](sprint/S2-L3.md) | Layer 3 구현 기록 (middleware 6개, worker, batch) |
| [sprint/S2-review.md](sprint/S2-review.md) | Sprint 2 회고 |

### Sprint 3 — CLI + MCP + Client 완성 (Layer 4-5)

| 문서 | 내용 |
|---|---|
| [sprint/S3-L4.md](sprint/S3-L4.md) | Layer 4 구현 기록 (client, batch, __init__) |
| [sprint/S3-L5.md](sprint/S3-L5.md) | Layer 5 구현 기록 (cli, mcp) |
| [sprint/S3-review.md](sprint/S3-review.md) | Sprint 3 회고 |

### Sprint 4 — 검증 + 배포

| 문서 | 내용 |
|---|---|
| [sprint/S4-V1.md](sprint/S4-V1.md) | test_app 직접 임포트 검증 (PyPI 배포 전) |
| [sprint/S4-D1.md](sprint/S4-D1.md) | PyPI 배포 (uv build + uv publish) |
| [sprint/S4-V2.md](sprint/S4-V2.md) | test_app 라이브러리 임포트 검증 (PyPI 배포 후) |
| [sprint/S4-review.md](sprint/S4-review.md) | Sprint 4 회고 |

## 프로젝트 설정

| 문서 | 내용 | 변경 시점 |
|---|---|---|
| [../CLAUDE.md](../CLAUDE.md) | 프로젝트 컨텍스트 — 아키텍처 요약, 의존성, 개발 명령어 | 구조/의존성/명령어 변경 시 |
| [../pyproject.toml](../pyproject.toml) | 빌드/의존성 정의 (hatchling + uv) | 의존성 추가/삭제/버전 변경 시 |
| [../.claude/settings.json](../.claude/settings.json) | Claude Code 허용 명령어 (uv, ruff, mypy, pytest 등) | 새 도구 추가 시 |
| [../.claude/rules/python-conventions.md](../.claude/rules/python-conventions.md) | Python/asyncio/pydantic/Redis 코딩 컨벤션 | 컨벤션 변경 시 |
| [../.claude/rules/architecture.md](../.claude/rules/architecture.md) | 아키텍처 규칙 — 6개 금지 사항, 레이어 의존 규칙 | 설계 원칙 변경 시 |
| [../.claude/rules/testing.md](../.claude/rules/testing.md) | 테스트 규칙 — pytest, fakeredis, PTY 테스트 | 테스트 정책 변경 시 |

## Claude Code 에이전트

| 에이전트 | 전문 분야 | 사용 시점 |
|---|---|---|
| [../.claude/agents/impl-L0.md](../.claude/agents/impl-L0.md) | 데이터 모델 (pydantic, 직렬화, 예외) | Sprint 1 Layer 0 구현 |
| [../.claude/agents/impl-L1.md](../.claude/agents/impl-L1.md) | 시스템 프로그래밍 (PTY, fork, 시그널) | Sprint 1 Layer 1 구현 |
| [../.claude/agents/impl-L2.md](../.claude/agents/impl-L2.md) | Redis/Lua/asyncio | Sprint 1 Layer 2 구현 |
| [../.claude/agents/impl-L3.md](../.claude/agents/impl-L3.md) | 미들웨어/워커 (체인, 재시도, 비용) | Sprint 2 Layer 3 구현 |
| [../.claude/agents/impl-L45.md](../.claude/agents/impl-L45.md) | API/CLI/MCP (사용자 인터페이스) | Sprint 3 Layer 4-5 구현 |
| [../.claude/agents/reviewer.md](../.claude/agents/reviewer.md) | 아키텍처 검증 (규칙 준수, 보안) | 레이어 구현 완료 후 |
| [../.claude/agents/verifier.md](../.claude/agents/verifier.md) | 통합 검증 (test_app, Docker, E2E) | Sprint 4 검증/배포 |

## Claude Code 스킬

| 스킬 | 명령어 | 내용 |
|---|---|---|
| [../.claude/skills/test.md](../.claude/skills/test.md) | `/test` | pytest 실행 (unit/integration/e2e) |
| [../.claude/skills/lint.md](../.claude/skills/lint.md) | `/lint` | ruff + mypy 린트/타입 체크 |
| [../.claude/skills/check.md](../.claude/skills/check.md) | `/check` | lint + test 전체 실행 |
| [../.claude/skills/sprint.md](../.claude/skills/sprint.md) | `/sprint` | 스프린트 진행 상태 + 레이어별 로그 |
| [../.claude/skills/docs-sync.md](../.claude/skills/docs-sync.md) | `/docs-sync` | 코드 ↔ 문서 동기화 검사 |
