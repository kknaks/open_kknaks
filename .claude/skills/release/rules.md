# 배포 규칙

## 절대 하지 말 것

1. **버전 리터럴을 직접 편집하지 마라.** `pyproject.toml`은 `dynamic = ["version"]`이고 `_version.py`는 hatch-vcs가 git 태그에서 자동 생성한다. 버전은 오직 git 태그(`vX.Y.Z`)로만 관리한다.
2. **같은 버전을 재태그하지 마라.** `git tag -f` / `git push -f --tags`는 금지. 같은 버전 번호의 코드가 두 번 PyPI에 올라가는 일이 없도록.
3. **태그된 커밋을 amend하지 마라.** 태그가 가리키는 SHA가 바뀌면 PyPI 산출물과 git 기록이 어긋난다. 수정이 필요하면 새 패치 버전으로 이어 간다.
4. **`--no-verify`로 hook을 우회하지 마라.** preflight는 안전망이다. 실패하면 원인을 고쳐라.
5. **`uv publish`를 로컬에서 직접 실행하지 마라.** GitHub Actions가 처리한다. 로컬 `dist/`에 옛 wheel이 남아있을 수 있어 잘못된 산출물이 올라갈 위험이 있다.
6. **태그를 main이 아닌 브랜치에서 찍지 마라.** 릴리즈는 항상 `main` 기준.

## SemVer 결정 규칙

| 변경 유형 | bump | 예시 |
|---|---|---|
| 공개 API 동작 변경, 시그니처 변경, 필드 제거/이름 변경 | **major** | `TaskResult.output` → `.result`/`.stream` |
| 새 기능 추가, 새 필드/메서드 추가, 호환되는 행동 개선 | **minor** | StreamEvent에 새 type 추가 |
| 버그 수정, 내부 리팩터링, 성능 개선 (관찰 가능한 동작 무변화) | **patch** | 메모리 누수 수정 |
| 문서/주석/CI 설정만 변경 | **bump 없음** | README 오타 수정 |

**판단 기준:** "기존 사용자 코드가 그대로 돌아가는가?" — 안 돌아가면 major. 그대로 돌아가지만 새로운 걸 쓸 수 있으면 minor. 사용자가 변화를 못 알아채면 patch.

## CHANGELOG.md 양식

major / minor 릴리즈는 반드시 `CHANGELOG.md`에 entry가 있어야 한다. 양식:

```markdown
## [X.Y.Z] — YYYY-MM-DD

### Breaking      (major에서만)
- ...

### Added
- ...

### Fixed
- ...

### Internal      (외부 사용자에게 영향 없는 변경)
- ...
```

patch 릴리즈는 entry 권장이지만 필수는 아님 (git log로 충분한 경우).

## 릴리즈 사이클

1. 기능/수정은 main에 작은 커밋으로 머지 (별도 브랜치 → PR → squash merge가 이상적이지만 현재는 main 직접 커밋 허용).
2. **여러 변경을 묶어 하나의 릴리즈로** — 매 커밋마다 태그 찍지 말 것. CHANGELOG가 의미 있는 단위로 정리되도록.
3. 태그 시점에 `CHANGELOG.md`에 해당 버전 섹션이 있어야 한다 (특히 minor/major).
4. 태그 push → GitHub Actions가 lint/test/build/publish/Release 생성을 모두 처리.

## major release 추가 요건

major bump은 사용자에게 영향이 크므로 추가 요건이 있다:

1. **CHANGELOG에 마이그레이션 가이드** — before/after 코드 스니펫 포함.
2. **실제 Claude CLI smoke test** — `scripts/smoke_v2.py` 같은 스크립트로 실 환경 검증. 합성 JSON 단위 테스트만으로는 부족.
3. **README 영향 검토** — README의 사용 예제가 깨지면 같이 업데이트.

## CI 실패 시 대응

GitHub Actions가 실패했을 때 절대 하지 말 것:
- 같은 태그 강제 재push (`git push -f`).
- 워크플로 우회/스킵.

대응:
- 실패 원인 파악 (`gh run view <run-id> --log-failed`).
- 로컬에서 동일 명령 재현하여 수정.
- 새 패치 버전으로 진행 (예: `v2.0.0` 실패 → 수정 → `v2.0.1` 태그).
- 만약 PyPI에 잘못된 산출물이 이미 올라갔다면 `pip yank` 또는 PyPI 콘솔에서 yank.
