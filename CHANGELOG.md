# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0] — 2026-08-04

### Fixed

- **tool_result 스트림 이벤트가 전부 유실되던 버그 수정.** Claude Code CLI 의 stream-json 은 도구 실행 결과를 최상위 `type:"tool_result"` 메시지가 아니라 `type:"user"` 메시지의 content 블록(`{"type":"tool_result","tool_use_id":...,"content":...,"is_error":...}`)으로 보냅니다. 파서에 `user` 분기가 없어 이 메시지가 전부 무시되어, 스트림에 tool_result 청크가 0건이었습니다(도구 실행 결과·성공/실패 여부 유실). 파서에 `user` 분기를 추가해 tool_result 블록마다 이벤트를 생성합니다 (content 는 str/list 모두 처리). 기존 최상위 `tool_result` 분기는 하위호환으로 유지됩니다.

### Added

- **`StreamEvent.tool_use_id` 필드 (optional).** `tool_use` 이벤트(assistant 블록의 `id`)와 `tool_result` 이벤트(블록의 `tool_use_id`)가 같은 id 를 실어, 소비자가 도구 호출과 결과를 짝지을 수 있습니다.

## [2.0.1] — 2026-05-12

### Fixed

- **variadic 옵션이 prompt 토큰을 흡수하던 버그 수정.** claude CLI 의 `--mcp-config <configs...>`, `--allowedTools <tools...>`, `--add-dir <dirs...>` 는 모두 variadic 이라, 그 중 하나가 build 결과의 마지막 옵션이면 그 다음에 박힌 positional prompt 가 옵션 인자로 흡수되었습니다. 예: `--mcp-config /tmp/cfg.json "안녕"` → CLI 가 `"안녕"` 을 두 번째 mcp config path 로 해석 → `MCP config file not found: /app/안녕` 에러. executor 가 prompt 박기 직전 `--` 를 박아 option parsing 을 종료하므로 어떤 variadic 옵션이 마지막에 와도 prompt 가 분리됩니다.

## [2.0.0] — 2026-04-30

### Breaking

- **`TaskResult.output` 필드 제거.** 대신 두 필드로 분리되었습니다:
  - `TaskResult.result` — Claude의 최종 result 메시지 텍스트만 담깁니다. 호출자가 일반적으로 원하는 값.
  - `TaskResult.stream` — 실행 중 본 모든 text 이벤트(delta + assistant 중간 메시지 + result)의 합본. 디버깅용.

  마이그레이션:

  ```python
  # before (v1.x)
  text = task_result.output

  # after (v2.0)
  text = task_result.result          # 깔끔한 최종 텍스트
  raw  = task_result.stream          # 기존 output 과 유사한 합본 (디버깅용)
  ```

  `Task.result` (사용자 향 Task 모델 필드)는 그대로이고, 워커가 내부적으로 `TaskResult.result`로 채웁니다.

### Fixed

- **stream-json 파서가 result 메시지의 텍스트를 silently drop하던 버그 수정.** 정상적인 Claude CLI 실행에서 `result` 메시지는 cost와 텍스트를 동시에 담는데, 기존 파서는 cost가 있으면 텍스트를 무시했습니다. 그 결과 `TaskResult.output`은 사실상 assistant 중간 메시지 모음이었습니다. 이제 `result` 메시지에서 cost 이벤트와 text 이벤트(`source="result"`)를 모두 emit합니다.
- **partial chunk 경계마다 `\n`이 박혀 한글이 글자 단위로 분절되던 문제 해결.** `--include-partial-messages` 사용 시 stream_event의 `text_delta`가 `output`에 누적되면서 `"안\n녕하세요"` 같은 분절이 발생했습니다. v2.0에서 `result` 필드는 result 메시지 텍스트만 사용하므로 이 분절이 사라집니다. partial은 여전히 `on_chunk` 콜백과 `stream` 필드로만 흐릅니다.

### Added

- 파서가 반환하는 text 이벤트에 `source` 필드 추가 (`"result" | "assistant" | "delta"`). 텍스트의 출처를 구분할 수 있습니다.
- `TestRealisticStreamAggregation` 회귀 테스트 — 한글 partial sequence가 깔끔한 result로 합쳐지는지 검증.

### Internal

- `pyproject.toml`의 ruff 설정에 `extend-exclude = ["open_kknaks/_version.py"]` 추가 (hatch-vcs 자동 생성 파일).

## [1.1.0] — 2026-04-06

- StreamEvent 8개 타입으로 확장, on_chunk 콜백 필터링 지원.

## [1.0.0] — 2026-04-06

- 초기 안정화 릴리즈.
