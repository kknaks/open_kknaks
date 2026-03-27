---
description: "린트 + 타입 체크 — ruff + mypy"
user_invocable: true
---

# /lint 스킬

유저가 `/lint` 를 실행하면 코드 품질 검사를 수행한다.

## 사용법

- `/lint` — ruff check + ruff format check + mypy 전체 실행
- `/lint fix` — ruff 자동 수정 + format 적용

## 실행

1. 프로젝트 루트(`open_kknaks/`)에서 순차 실행:

```bash
# 체크 모드 (기본)
uv run ruff check open_kknaks/ tests/
uv run ruff format --check open_kknaks/ tests/
uv run mypy open_kknaks/

# 자동 수정 모드 (/lint fix)
uv run ruff check --fix open_kknaks/ tests/
uv run ruff format open_kknaks/ tests/
uv run mypy open_kknaks/
```

2. 에러가 있으면:
   - ruff 위반: 자동 수정 가능하면 `--fix`로 수정, 아니면 유저에게 알림
   - mypy 에러: 관련 코드를 읽고 타입 힌트 수정 제안
   - format 위반: `ruff format`으로 자동 적용

3. 전체 통과하면 결과 요약만 보여준다
