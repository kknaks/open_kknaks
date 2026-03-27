---
description: "스프린트 진행 상태 확인 + 스프린트 문서 관리"
user_invocable: true
---

# /sprint 스킬

유저가 `/sprint` 를 실행하면 현재 스프린트의 구현 진행 상태를 확인한다.

## 사용법

- `/sprint` — 현재 스프린트 전체 상태
- `/sprint L0` — 특정 레이어 상태만
- `/sprint log {내용}` — 현재 레이어의 스프린트 문서에 기록 추가

## 문서 구조

스프린트 문서는 `docs/sprint/` 에 레이어별로 관리된다:

```
docs/sprint/
├── S1-L0.md    — Layer 0 (task, config, exceptions, line_buffer)
├── S1-L1.md    — Layer 1 (pty_process, stream_parser, broker/base)
├── S1-L2.md    — Layer 2 (executor, redis broker, lua)
├── S1-review.md — 회고
```

각 레이어 문서에는:
- 구현 결정사항 및 이유
- 발견된 이슈 및 해결
- 인터페이스 변경 이력
- 다음 레이어에 전달할 주의사항

## 실행 흐름

1. `docs/PLAN.md`를 읽어 현재 스프린트의 작업 목록을 파악한다
2. 각 작업에 해당하는 파일이 실제로 존재하는지 확인한다
3. 존재하는 파일에 대해 핵심 클래스/함수가 구현되어 있는지 검사한다
4. 테스트 파일이 있는지, 통과하는지 확인한다
5. 해당 레이어의 스프린트 문서(`docs/sprint/S{n}-L{n}.md`)와 대조한다

## 출력 형식

```
## Sprint 1 — PTY Executor + 코어

### Layer 0 (task, config, exceptions, line_buffer)
| # | 작업 | 상태 | 비고 |
|---|---|---|---|
| 1-1 | 프로젝트 세팅 | done | pyproject.toml, ruff, mypy 설정 완료 |
| 1-2 | 데이터 모델 | done | task.py, config.py, exceptions.py |
| 1-3 | LineBuffer | in progress | 구현 완료, 테스트 2/5 통과 |

### Layer 1 (pty_process, stream_parser, broker/base)
| # | 작업 | 상태 | 비고 |
|---|---|---|---|
| 1-4 | PTYProcess | not started | L0 완료 후 시작 |
| ... | ... | ... | ... |

진행률: 3/10 (30%)
```

6. 미완료 작업 중 다음에 할 수 있는 것(의존성 충족된 것)을 알려준다

## /sprint log 사용법

```
/sprint log "PTYProcess.terminate()에서 os.waitpid(-pgid) 추가. 그룹 전체 좀비 수거."
```

→ 현재 진행 중인 레이어 문서에 타임스탬프와 함께 기록:

```markdown
### 2026-03-26
- PTYProcess.terminate()에서 os.waitpid(-pgid) 추가. 그룹 전체 좀비 수거.
```
