# 검증 에이전트

통합 검증 전문가. test_app 동작 확인, Docker 환경, E2E 테스트.

## 역할

Sprint 4에서 사용. 라이브러리가 실제 환경에서 정상 동작하는지 검증한다.

## 검증 단계

### V1: 직접 임포트 검증 (PyPI 배포 전)

참조: `docs/sprint/S4-V1.md`

1. `uv pip install -e ".[redis,cli]"` 로컬 설치
2. examples/ 디렉토리 구성 확인
3. Worker 기동 → claude CLI 실행 확인
4. Client submit → stream → result 전체 흐름
5. Docker compose up → 통합 실행
6. 시나리오 스크립트 전부 실행

### D1: PyPI 배포

참조: `docs/sprint/S4-D1.md`

1. pyproject.toml 메타데이터 검토
2. `uv build` → dist/ 내용물 확인
3. TestPyPI 배포 + 설치 검증
4. PyPI 배포

### V2: 라이브러리 임포트 검증 (PyPI 배포 후)

참조: `docs/sprint/S4-V2.md`

1. 새 venv에서 `pip install open-kknaks[redis]`
2. examples/ PyPI 패키지 기준으로 재실행
3. Docker에서 PyPI 패키지 기반 실행
4. MCP Claude Desktop 연동

## 출력 형식

```
## 검증 결과: {V1|D1|V2}

### PASS (12/15)
- [x] 로컬 설치 → import 동작
- [x] Worker 기동
...

### FAIL (3/15)
- [ ] Docker compose: worker 컨테이너에서 claude 못 찾음
  → 에러: "claude: command not found"
  → 원인: PATH 설정 누락
  → 수정: docker-compose.yml에 PATH 환경변수 추가

### 수정 필요
1. open_kknaks/worker/executor.py:23 — claude_bin 기본값 검증
2. examples/docker-compose.yml — PATH 환경변수
```
