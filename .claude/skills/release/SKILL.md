---
description: "PyPI 배포 — git tag push 한 번으로 GitHub Actions가 lint/test/build/publish 자동 진행"
user_invocable: true
---

# /release 스킬

`open-kknaks`를 PyPI에 배포한다. 직접 `uv publish`를 호출하지 않는다 — `v*` 태그를 push하면 `.github/workflows/release.yml`이 트리거되어 lint/test/build/publish/GitHub Release 생성을 모두 처리한다.

## 사용법

```
/release 2.0.0          # 명시적 버전 (권장)
/release minor          # 마지막 태그에서 minor bump
/release patch          # 마지막 태그에서 patch bump
/release major          # 마지막 태그에서 major bump
```

## 한 줄 요약

> "코드 push → CHANGELOG 확인 → preflight (lint/test) → 태그 push" 순서로 실행하고, 그 뒤로는 GitHub Actions에 맡긴다.

## 실행 흐름

### 1. 사전 점검 (자동)

`scripts/preflight.sh`를 실행한다. 다음이 모두 통과해야 진행:
- `git status`가 clean (staging/working tree 둘 다)
- 현재 브랜치가 `main`
- `origin/main`과 동기화됨 (또는 ahead만)
- `ruff check` ✓
- `ruff format --check` ✓
- `mypy --strict` ✓
- `pytest --ignore=tests/e2e` ✓

하나라도 실패하면 즉시 멈추고 사용자에게 알린다.

### 2. SemVer 검증

`rules.md`의 SemVer 규칙대로 bump이 적절한지 확인한다.
**BREAKING change(major bump)인 경우** — `CHANGELOG.md`에 해당 버전 entry가 작성되어 있어야 한다. 없으면 멈춘다.

### 3. 코드 push

```bash
git push origin main
```

태그를 찍기 전에 main을 먼저 push한다 (태그가 가리키는 커밋이 원격에 존재해야 안전).

### 4. 태그 생성 + push

```bash
git tag -a v$VERSION -m "Release v$VERSION"
git push origin v$VERSION
```

tag push 시 `.claude/settings.json`의 PreToolUse hook이 한 번 더 preflight을 자동 실행한다 (이중 안전망).

### 5. GitHub Actions 모니터링

```bash
gh run list --workflow=release.yml --limit 1
gh run watch  # 진행 상황 실시간
```

워크플로 완료 후:
- PyPI 페이지 확인: https://pypi.org/project/open-kknaks/
- GitHub Release 확인: `gh release view v$VERSION`

## 실패 시

| 단계 | 실패 원인 | 대응 |
|---|---|---|
| preflight | lint/test 실패 | 코드 수정 후 다시 commit하고 재시도. 절대 `--no-verify`로 우회하지 마라. |
| push (코드) | `non-fast-forward` | `git pull --rebase` 후 재시도. 강제 push 금지 (main에서). |
| 태그 push | 같은 태그 이미 존재 | **재태그 금지.** 다음 버전 번호로 진행. yank만 가능한 PyPI에 같은 코드가 다른 버전으로 올라가는 일은 없도록. |
| Actions 실패 | CI 단계에서 깨짐 | 로컬에서 동일 명령으로 재현 → 수정 → 새 commit + 새 태그 (`v2.0.1` 등). 이미 PyPI에 publish 되었다면 `pip yank`로 회수. |

## 관련 파일

- `rules.md` — 배포 규칙 (SemVer, 절대 하지 말 것, CHANGELOG 양식)
- `scripts/preflight.sh` — 사전 점검
- `scripts/release.sh` — 위 흐름을 한번에 실행
- `scripts/pre_tag_hook.sh` — Claude Code PreToolUse hook (git tag/push --tags 실행 시 preflight 자동 호출)
- `examples/v2.0.0.md` — 실제 BREAKING release 워크드 예시

## 직접 호출하기 vs 스킬 호출

`/release 2.0.0` 한 줄로 끝낸다. 만약 단계별로 직접 보고 싶다면:

```bash
bash .claude/skills/release/scripts/preflight.sh
git push origin main
git tag -a v2.0.0 -m "Release v2.0.0"
git push origin v2.0.0
gh run watch
```
