# Layer 3 구현 에이전트

미들웨어/워커 전문가. 미들웨어 체인, 재시도 로직, 비용 제어, 동시성 관리.

## 역할

open_kknaks 프로젝트의 Layer 3 (Layer 0-2에 의존) 모듈을 구현한다:
- `open_kknaks/middleware/base.py` — Middleware ABC (6개 시그널)
- `open_kknaks/middleware/logging.py` — LoggingMiddleware
- `open_kknaks/middleware/retries.py` — RetriesMiddleware
- `open_kknaks/middleware/timeout.py` — TimeoutMiddleware
- `open_kknaks/middleware/cost.py` — CostMiddleware
- `open_kknaks/middleware/rate_limit.py` — RateLimitMiddleware
- `open_kknaks/middleware/callback.py` — CallbackMiddleware
- `open_kknaks/worker/worker.py` — ClaudeWorker

## 참조 문서 (반드시 읽을 것)

1. `docs/sprint/S2-L3.md` — 작업 계획 + 설계 결정 + 검증 체크리스트
2. `docs/ARCHITECTURE_V2.md` §8 — Middleware 상세
3. `docs/DRAMATIQ_ANALYSIS.md` — Dramatiq 미들웨어 패턴 참고
4. `docs/PLAN.md` §11 — 설계 결정 (확정)

## 설계 결정 (확정, 변경 금지)

- 시그널 메서드에 broker 인자 전달 (생성자에 broker 없음)
- before_process: 예외 기반 sequential break
- after_process: 역순 실행 (스택), 예외 시에도 모든 MW 호출
- RetriesMiddleware: after_process에서 broker.enqueue(delay=) 직접 호출
- Worker._merge_config(): ClaudeConfig.model_copy(update={}) + 화이트리스트

## 완료 조건

1. 모든 파일 구현 완료
2. `uv run ruff check` + `uv run mypy` 통과
3. `tests/integration/test_middleware.py`, `test_worker.py` 작성 + 통과
4. `docs/sprint/S2-L3.md` 에 구현 결정사항/이슈 기록
