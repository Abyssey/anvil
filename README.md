# Anvil — Coding Agent

로컬 레포지토리를 탐색·계획·수정·검증하고 턴 단위로 안전하게 되돌릴 수 있는 웹 워크벤치형 코딩 에이전트입니다.
**FastAPI 0.165**, **LangChain DeepAgents (`0.7.41`)**, **LangGraph (`AsyncSqliteSaver`)**, **React 19 + Astryx (`0.7.17`)** 기반으로 구축되었습니다.

## 문서 바로가기 (Wiki)

- **[시스템 아키텍처 (`docs/architecture.md`)](docs/architecture.md)** — 4개 DDD Bounded Context, 하네스·미들웨어 파이프라인, 3계층 Revert, 프론트엔드 파생 뷰 구조
- **[구현 회고 & 배운 점 (`docs/what-i-learned.md`)](docs/what-i-learned.md)** — 코딩 에이전트를 직접 구현하며 배운 핵심 설계 원리와 시행착오 정리
- **[유비쿼터스 용어집 (`GLOSSARY.md`)](GLOSSARY.md)** — 도메인 핵심 개념 정의

---

## 핵심 기능

- **3단계 실행 모드 (`Ask` / `Plan` / `Auto`)**
  - **`Ask`**: 파일 수정·셸 실행이 차단된 읽기 전용 병렬 탐색 모드(`ls`, `glob`, `grep`, `read_file`, `explorer` 서브에이전트)로 빠른 코드베이스 분석 및 `walkthrough.md` 생성
  - **`Plan`**: 요구사항이 모호하면 선택지 질문 카드(`ask_user`)로 먼저 확인한 뒤 `implementation_plan.md`를 작성하고, 사용자 승인(`Proceed` / `Request Changes`) 후 구현 및 검증 진행
  - **`Auto`**: 탐색부터 수정·검증까지 자율 수행하되, 파괴적 명령(`rm`, `git push`, `git reset --hard` 등)과 검증 규칙 파일(`AGENTS.md`, 아키텍처 경계 테스트 등) 수정은 반드시 사용자 승인을 거침
- **검증 피드백 루프 & 3계층 동기화 Revert**
  - 코드 수정과 테스트/린트 실행 결과를 추적해 턴별 검증 배지(`Verified` / `Checks failed`)를 표시하고, 연속 3회 실패 시 무분별한 재시도를 멈추는 **Three-Strike 규칙** 적용
  - 턴 단위 **Revert** 실행 시 SHA-256 스냅샷 기반 워크스페이스 파일 복원 + SQLite 메시지 절삭 + LangGraph 스레드 재동기화 + 프롬프트 입력창 복원을 동시에 수행하며, `Diff` 드로어에서 파일별·전체 Git 변경 되돌리기도 지원
- **에이전트 스킬(`skills.sh`) & 2계층 메모리 격리**
  - 내장 스킬(`codebase-onboarding`, `verify`) 및 `skills.sh` 카탈로그 검색·설치·`/skill-name` 핀 고정 지원
  - 세션 단기 기억(`/artifacts/`, 대화 스레드)과 프로젝트 장기 기억(`/memories/project/AGENTS.md`, 레포지토리 `/AGENTS.md`)을 엄격히 분리해 프로젝트 간 컨텍스트 오염 방지
- **클린 스트리밍 워크벤치 UI & 서브에이전트 카드**
  - 연속 읽기·탐색 도구 호출을 1줄 아코디언(`ExplorationGroup`)으로 자동 병합하고, 병렬·독립 서브에이전트(`Explorer` · `Code Reviewer`) 실행은 Claude Code 스타일의 전용 카드(`SubagentGroupView` — 역할 배지, 도구 호출 수·소요 시간 요약, 내부 실행 도구 트레이스, `[Important]`/`[Nit]` 마크다운 리뷰 리포트)로 분리 표시
  - 턴별 소요 시간(`Worked for ...`), 다크 코드 블록(`workbench` 테마), `pele/lazy` 기반 경량 Mermaid 다이어그램 렌더링
  - API 키는 브라우저(`localStorage`)에만 보관하고 요청 헤더(`X-LLM-*`)로만 전달하며, 서버 DB·체크포인트·로그(`SecretRedactionFilter`)에 평문 저장하지 않음

---

## 빠른 시작 (Linux / WSL2)

> WSL2 비대화형 셸에서 Linux용 Node/npm을 사용하려면 `~/.local/bin`을 `PATH` 앞에 추가하세요:
> ```bash
> export PATH="$HOME/.local/bin:$PATH"
> ```

### 1. 프론트엔드 빌드 및 통합 서버 실행

```bash
export PATH="$HOME/.local/bin:$PATH"
cd frontend && npm install && npm run build && cd ..
.venv/bin/fastapi run backend/app/main.py --port 8000
```

브라우저에서 `http://localhost:8000`에 접속합니다. FastAPI 0.165가 `/api/*` 엔드포인트와 빌드된 프론트엔드(`app.frontend()`)를 단일 프로세스로 서빙하며, 기본값으로 런타임 데이터는 `.data/sessions.db`(`WAL` 저널 모드) 아래에 격리 저장됩니다.

### 2. 스토리지 환경변수 (로컬 vs Agentic Works NAS)

스토리지 경로와 SQLite 저널 모드는 **환경변수로만** 설정하며(웹 UI 노출 없음), 대화 DB(`SQLiteSessionRepository`)와 LangGraph 체크포인터(`AsyncSqliteSaver`) 양쪽에 동일하게 적용됩니다. 지정 경로에 쓰기 권한이 없거나 접근에 실패하면 서버 기동이 즉시 중단됩니다.

| 환경변수 | 기본값 (로컬) | Agentic Works (NAS 마운트 · 단일 Pod/프로세스) |
| :--- | :--- | :--- |
| `DATA_DIR` | `<repo_root>/.data` | NAS 절대 경로 (예: `/mnt/nas/anvil-data`) |
| `SQLITE_JOURNAL_MODE` | `WAL` | `DELETE` |

```bash
# Agentic Works (AWX) NAS 실행 예시
DATA_DIR=/mnt/nas/anvil-data SQLITE_JOURNAL_MODE=DELETE \
  .venv/bin/fastapi run backend/app/main.py --port 8000
```

### 3. LLM 엔드포인트 설정

좌측 하단 **Configure LLM** 버튼을 눌러 OpenAI 호환 엔드포인트 정보를 입력합니다:
- **Endpoint URL**: 예) `http://localhost:8001/v1` 또는 OpenAI 호환 게이트웨이 URL
- **Model ID**: 예) `qwen3.8-flash` 등 사용 모델 ID
- **API Key**: 발급받은 API 키 (브라우저에만 저장됨)
- **Enable Thinking**: 추론 트레이스(`reasoning_content`) 스트리밍 사용 여부

---

## 검증 명령어

```bash
# 백엔드 단위·불변식·통합 테스트 및 DDD AST 경계 검증 (오프라인 결정론적 실행)
.venv/bin/pytest -q

# 백엔드 린트
.venv/bin/ruff check backend tests

# 프론트엔드 타입체크, 테스트, 프로덕션 빌드 및 Astryx 감사
export PATH="$HOME/.local/bin:$PATH" && cd frontend && npm run typecheck && npm test -- --run && npm run build && npx astryx doctor --built .
```
