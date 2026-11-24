# 코딩 에이전트 프로젝트 개요

> 상세 내부 구조와 동작 원리는 **[코딩 에이전트 구현 Review (`docs/coding-agent-study.md`)](coding-agent-study.md)**를 참고하세요.

1. 학습 목표
   - 일반 챗봇·RAG와 달리 파일을 직접 수정하고 프로세스를 실행하는 자율 에이전트의 핵심 구조 체득
   - 대규모 코드베이스를 최소 토큰으로 다루는 Context Engineering(`Progressive Disclosure` · `Prefix Cache` · `Subagent` 격리) 실습
   - 부분 치환(`edit_file`) 및 자가 교정 피드백 중심의 도구(`ACI`) 설계와 실행 중 인터럽트(`Suspend / Resume`) · `Revert` 제어 구현

2. Agent 간략 설명
   - 로컬 Git 저장소를 대상으로 자연어 지시를 받아 코드 탐색·수정·테스트 검증까지 수행하는 웹 기반 코딩 에이전트
   - input: 버그 수정, 기능 추가, 리팩토링, 코드베이스 구조 분석 요청
   - output: 실제 코드 변경분(`Diff`), 구현 계획서(`implementation_plan.md`), 작업 및 검증 요약(`walkthrough.md`)
   - 주요 특징
     - 작업 성격에 따른 `Ask`(읽기 전용) · `Plan`(질의 및 계획 승인 후 수정) · `Auto`(자율 수행 + 위험 명령 승인) 모드 분리
     - 실행 중 후속 지시 대기열(`Queue`) 적재 및 진행 중인 루프에 즉시 개입하는 실시간 방향 전환(`Mid-Run Steering`) 지원
     - 읽기 전용 서브에이전트(`Explorer` · `Code Reviewer`)를 통한 문맥 오염 방지 및 객관적 코드 검토
     - 테스트 연속 실패 제한(`3-Strike`) 및 파일·대화 DB·체크포인터 기억을 함께 되돌리는 턴 단위 `Revert` 지원
     - FastAPI 단일 서버 + Astryx 기반 웹 UI 제공 (브라우저 설정을 통한 OpenAI 호환 LLM 연동)

3. 개발 일정
   - 10월 말: 학습 목표 수립, 최근 코딩 에이전트(Claude Code 등) 내부 구조 조사 및 FastAPI · LangGraph 기본 환경 세팅
   - 11월 초: 파일 탐색·부분 치환(`edit_file`)·셸 실행 백엔드 구성 및 실시간 SSE 스트리밍 루프 구현
   - 11월 중: 실행 모드(`Ask` · `Plan` · `Auto`) 분리, HITL 인터럽트(`ask_user` · 위험 명령 승인), 스킬(`SKILL.md`) 및 2계층 메모리 구현
   - 11월 말: Subagent 격리, 턴 단위 동기화 `Revert`, 실행 중 `Queue` · `Steer` 방향 조정 및 UI · 런타임 안정화

4. 개발 로그
   - (10/22) 개발 환경 세팅, Claude Code 등 최근 코딩 에이전트 컨텍스트·도구 설계 조사 및 기본 FastAPI 서버 생성
   - (10/26) 프로젝트·세션 SQLite 스키마 작성 및 OpenAI 호환 LLM 연동 · SSE 스트리밍 기본 루프 구현
   - (10/29) 로컬 파일 탐색(`ls` · `glob` · `grep` · `read_file`) · 부분 치환(`edit_file`) 및 가상 경로(`DynamicRunBackend`) 연결
   - (11/3) `Ask` · `Plan` · `Auto` 실행 모드 분리, 위험 명령 필터(`ShellGuard`) 및 LangGraph `interrupt()` 기반 승인·질문(`ask_user`) 추가
   - (11/6) Astryx 기반 채팅 UI 연동, 프롬프트 Prefix Cache 정렬 및 `AGENTS.md` · `SKILL.md`(`skills.sh`) 지연 로딩 적용
   - (11/9) 테스트 자동 검증 루프(`3-Strike`) 및 파일 스냅샷·대화 DB·체크포인터를 함께 되감는 턴 단위 `Revert` 구현
   - (11/19) 백엔드 도메인(DDD) 레이어 분리 및 AST 경계 테스트 작성, 채팅 내 Mermaid 다이어그램 잘림 수정 및 휠 줌 뷰어 보완
   - (11/23) 읽기 전용 Subagent(`Explorer` · `Code Reviewer`) 분리, 실행 중 `Queue` · `Steer` 개입, 대형 스킬 레포 스트리밍 추출 및 위험 동작 확인 모달(`AlertDialog`) 보완

5. 향후 확장 과제
   - 초장기 세션 자동 요약 압축 (`Context Compaction`): 수십 턴 이상 누적된 과거 도구 출력을 요약본으로 교체해 컨텍스트 한도 도달 방지
   - Git Worktree 기반 병렬 세션 격리: 동일 저장소에서 여러 세션이 서로 다른 기능을 충돌 없이 동시에 수정·검증하는 워크트리 분리 실행
