---
description: "전체 체크 — lint + test 한번에 실행"
user_invocable: true
---

# /check 스킬

유저가 `/check` 를 실행하면 린트 + 타입 체크 + 테스트를 한번에 수행한다.
코드 커밋/PR 전에 돌리는 용도.

## 실행

프로젝트 루트(`open_kknaks/`)에서 순차 실행:

```bash
# 1. 린트
uv run ruff check open_kknaks/ tests/

# 2. 포맷 체크
uv run ruff format --check open_kknaks/ tests/

# 3. 타입 체크
uv run mypy open_kknaks/

# 4. 테스트 (E2E 제외)
uv run pytest tests/ -v --tb=short -m "not e2e"
```

각 단계가 실패하면 즉시 멈추고 해당 에러를 보여준다.
전체 통과하면 한 줄 요약만 출력한다.
