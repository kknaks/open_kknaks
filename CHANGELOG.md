# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.1] — 2026-08-05

### Fixed

- **codex provider 의 tool 이벤트에 `tool_use_id` 가 채워지지 않던 버그 수정.** 2.1.0 의 `tool_use_id` 는 claude 파서(`stream_parser.py`) 전용이라, `CodexRunnerAdapter` 가 만드는 `tool_use`/`tool_result` 이벤트는 항상 `tool_use_id=None` 이었습니다(소비자가 도구 호출과 결과를 짝지을 수 없음). 이제 codex 이벤트의 `item.id` 를 실어 보냅니다 — `item.started` 와 `item.completed` 가 같은 id 를 쓰므로 claude 경로와 동형으로 짝지어집니다.
- **codex 0.146 의 flat item 을 파싱하지 못해 tool 이벤트가 아예 유실되던 버그 수정.** 어댑터는 tool payload 가 `item.details` 에 중첩된 형태만 인식했지만, 실제 `codex exec --json` (0.146.0 실측)은 `{"id","type":"command_execution","command",...}` 처럼 `item` 최상위에 flat 하게 보냅니다. 그래서 도구 실행이 전부 `progress` 이벤트로 떨어지고 `tool_use`/`tool_result` 는 0건이었습니다. 이제 flat/중첩 두 모양을 모두 지원하고, 결과 텍스트는 `aggregated_output` 도 읽으며, `exit_code`/`status` 로 `tool_is_error` 를 판정합니다.
- **`{ns}:stream:{task_id}` 키가 TTL 없이 영구히 쌓이던 누수 수정.** `{ns}:task:{id}` 는 ack 시 `result_ttl` EXPIRE 가 걸렸지만 stream 키는 어떤 경로에서도 만료되지 않아 TTL=-1 로 단조 증가했습니다. ack 시 stream 키에도 `result_ttl` EXPIRE 를 겁니다. 종결(ack/nack) 시점에만 걸므로 소비 중인 스트림이 잘리지 않습니다.
- **codex resume 제출이 항상 exit 2 로 죽던 버그 수정.** `codex exec resume` 는 `codex exec` 플래그의 진부분집합만 받는데, 어댑터가 resume 모드에서도 sandbox 기본값(`workspace-write`)·`--cd` 를 그대로 박아 세션을 열기도 전에 `error: unexpected argument` 로 종료됐습니다(소비자는 `provider_options={"sandbox": ""}` 로 회피 중이었습니다). 이제 resume 모드에서는 resume 가 거부하는 옵션(`--sandbox`, `--cd`, `--add-dir`, `--color`, `--profile`, `--profile-v2`, `--local-provider`, `--oss`)을 emit 하지 않습니다. codex-cli 0.146.0 실측 기준 resume 가 받는 `--model`, `--skip-git-repo-check`, `--ephemeral`, `--config`, `--image`, `--output-last-message`, `--output-schema` 등은 그대로 실립니다. 신규 세션 모드의 동작은 변경 없습니다.
- **codex usage 의 캐시·reasoning 토큰이 항상 0 으로 집계되던 버그 수정.** 어댑터가 `cache_read_tokens`/`cache_write_tokens` 를 찾았지만 codex 가 실제로 보내는 키는 `cached_input_tokens`/`cache_write_input_tokens`/`reasoning_output_tokens` 라, 매칭되는 키가 하나도 없어 캐시 토큰이 전부 유실됐습니다(0.146.0 실측: `{"input_tokens":15059,"cached_input_tokens":11008,"cache_write_input_tokens":0,"output_tokens":5,"reasoning_output_tokens":0}`). 이제 5개 키를 모두 `TaskResult.usage` 로 전달합니다. 옛 키 이름도 fallback 으로 계속 읽습니다. 주의: `cached_input_tokens` 는 `input_tokens` 의 부분집합이고 `reasoning_output_tokens` 는 `output_tokens` 의 부분집합이라 합산하면 중복 계상됩니다.
- **claude usage 의 캐시 토큰이 항상 0 으로 집계되던 버그 수정.** codex 와 같은 결함이 claude 파서에도 있었습니다 — `stream_parser.py` 가 `cache_read_tokens`/`cache_write_tokens` 를 찾았지만 실제 stream-json 의 result 메시지는 `cache_read_input_tokens`/`cache_creation_input_tokens` 를 보냅니다(실측: `{"input_tokens":2,"cache_creation_input_tokens":9101,"cache_read_input_tokens":15272,"output_tokens":4}` → 라이브러리는 0/0 으로 집계). 실제 키를 정규화 필드로 매핑하고, 옛 키 이름은 fallback 으로 계속 읽습니다.

- **claude 실행 비용이 항상 0.0 으로 집계돼 CostMiddleware 예산 제어가 무력화되던 버그 수정.** 파서가 result 메시지에서 `cost_usd` 를 읽었지만 CLI 가 실제로 보내는 키는 `total_cost_usd` 입니다(실측: `total_cost_usd=0.026029` → 라이브러리는 `0.0` 으로 파싱). 이 값이 그대로 `TaskResult.usage.cost_usd` 로 흘러 `CostMiddleware` 의 예산 집행과 `broker.incr_cost` 를 먹이므로, **누적 비용이 영원히 0 이라 worker/global 예산 상한이 발동할 수 없었습니다.** 이제 `total_cost_usd` 를 우선 읽고 `cost_usd` 는 fallback 으로 유지합니다. 캐시 토큰 수정과 마찬가지로 실제 0.0 이 fallback 으로 새지 않도록 «키 존재» 로 판정합니다.
- **nack(실패) 태스크의 task/stream 키에도 TTL 적용.** 기존에는 DLQ 로 간 태스크의 키가 무기한 남았습니다. 이제 새 `dlq_ttl` (기본 7일) 로 만료되며, DLQ 조회·재시도에 필요한 시간을 확보하려고 `result_ttl` 보다 길게 잡았습니다.

### Changed

- **`TokenUsage` 의 provider 간 집계 의미를 문서화.** 필드 이름은 정규화돼 있지만 회계 방식이 다릅니다 — claude 는 `cache_read_tokens`/`cache_write_tokens` 가 `input_tokens` 와 **별개 축**(총 입력 ≈ input + cache_read + cache_write)이고, codex 는 `cache_read_tokens` 가 `input_tokens` 의 **부분집합**(`reasoning_output_tokens` 도 `output_tokens` 의 부분집합)입니다. provider 를 가로질러 합산할 때 이 차이를 고려해야 합니다.

### Added

- `RedisBroker(dlq_ttl=...)` 파라미터 (기본 `7 * 24 * 3600`). 실패 태스크의 task/stream 키 보존 기간.
- **`TokenUsage.reasoning_output_tokens` 필드 (기본 0).** codex 가 보고하는 reasoning 토큰을 담습니다. 기존 필드에 대응되는 자리가 없어 codex 원 키 이름 그대로 추가했습니다. 이 값을 보고하지 않는 provider 에서는 0 입니다.

### Internal

- `enqueue.lua` 가 task/stream 키에 `PERSIST` 를 겁니다. `HSET`/`XADD` 는 기존 TTL 을 지우지 않으므로, DLQ 재시도된 태스크가 실행 도중 만료되는 것을 막습니다.
- codex `item.updated` 는 더 이상 `tool_result` 를 만들지 않습니다 (중간 스냅샷이라 같은 `tool_use_id` 로 중복 결과가 나갔습니다). `progress` 이벤트로 나갑니다.

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
