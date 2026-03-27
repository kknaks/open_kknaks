---
description: "테스트 실행 — pytest 기반 단위/통합/E2E 테스트"
user_invocable: true
---

# /test 스킬

유저가 `/test` 를 실행하면 프로젝트 테스트를 수행한다.

## 사용법

- `/test` — 전체 테스트 (E2E 제외)
- `/test unit` — 단위 테스트만
- `/test integration` — 통합 테스트만 (fakeredis)
- `/test e2e` — E2E 테스트 (실제 claude CLI 호출)
- `/test {파일경로}` — 특정 파일만
- `/test -k {패턴}` — 패턴 매칭

## 실행

1. 인자를 파싱하여 적절한 pytest 명령을 구성한다
2. 프로젝트 루트(`open_kknaks/`)에서 실행한다

```bash
# 전체 (E2E 제외)
uv run pytest tests/ -v --tb=short -m "not e2e"

# 단위
uv run pytest tests/unit/ -v --tb=short

# 통합
uv run pytest tests/integration/ -v --tb=short

# E2E
uv run pytest tests/e2e/ -v --tb=short -m e2e

# 특정 파일
uv run pytest {파일경로} -v --tb=short

# 패턴
uv run pytest tests/ -v --tb=short -k "{패턴}"
```

3. 실패한 테스트가 있으면:
   - 실패 메시지를 분석한다
   - 관련 소스 코드를 읽고 원인을 파악한다
   - 수정이 필요하면 유저에게 알린다 (자동으로 고치지 않음)

4. 전체 통과하면 결과 요약만 보여준다
