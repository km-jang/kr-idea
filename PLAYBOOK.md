# 장애 대응 플레이북 (PLAYBOOK.md)

시스템이 멈추거나 이상할 때 이 문서 하나로 진단→복구한다.
AI에게 맡길 땐 "PLAYBOOK.md 보고 ○○ 증상 고쳐줘"라고 하면 된다.

## 0. 만능 진단 순서 (어떤 증상이든 여기부터)

1. `github.com/km-jang/kr-idea/actions` → 최근 실행이 초록인지 빨강인지
2. 빨강이면: 실행 클릭 → `update` → 빨간 단계 클릭 → 에러 로그 확인
3. 초록인데 이상하면: 각 단계 로그의 마지막 줄 확인 (스킵/생략 문구가 단서)
4. 대시보드 상단의 "장 마감 기준" 날짜 확인 — 며칠 전이면 수집이 안 돌고 있는 것

## 1. 대시보드가 며칠째 안 바뀜

- **Actions 실행 자체가 없음** → GitHub는 저장소에 60일간 활동이 없으면 예약 실행을 끈다.
  Actions 탭에 노란 배너의 `Enable workflow` 버튼을 누르면 재개. (평소엔 매일 커밋이 생겨 해당 없음)
- **실행은 있는데 빨강** → 아래 2번.
- **초록인데 "휴장일/중복 실행 감지"만 반복** → 연휴면 정상. 평일에도 계속 그러면
  지수 API가 옛 날짜를 주는 것 → AI에게 `fetch_indices`/`is_holiday_rerun` 점검 요청.

## 2. 수집 실패 (빨간 X, "데이터 수집" 단계)

### 로그에 "유니버스 수집 실패"
네이버 목록 API가 바뀐 것. 임시로는 아무것도 안 해도 됨 (직전 데이터 유지).
복구: AI에게 이렇게 요청 —
> "네이버 유니버스 API가 깨졌어. collect.py의 fetch_universe / parse_market_value_item을
> 현재 응답 구조에 맞게 고치고, 폴백(fetch_universe_fallback)도 점검해줘."
대체 소스 우선순위: ① 네이버 데스크톱 페이지(폴백 내장) ② 다음 금융 API ③ KRX 정보데이터시스템(무료 계정 필요)

### 로그에 "수집 품질 미달 - 갱신 중단"
안전장치가 정상 작동한 것 (반쪽 데이터 배포 방지). 하루 이틀 지나도 반복되면
어떤 항목이 미달인지 로그에 나옴 (유니버스/수급/펀더멘털) → 해당 수집 함수 수리 요청:
- 수급 미달 → `fetch_investor_flows` / `parse_frgn_html`
- 펀더멘털 미달 → `fetch_fundamentals` / `parse_integration`

### 업종만 안 나옴 (대시보드 "업종 데이터는 다음 갱신부터")
wiseindex.com 장애/변경. 업종 섹션만 빠지고 나머진 정상 — 급하지 않음.
복구 요청: "fetch_sector_map / parse_sector_items를 점검해줘."

## 3. 텔레그램이 안 옴

실행이 **초록**인데 안 오면 → "아침 브리핑 발송" 단계 로그 마지막 줄이 답:
- `시크릿이 없어 발송 생략` → Settings → Secrets and variables → Actions의
  **Repository secrets**에 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` 재등록
  (이름은 영문 대문자+밑줄, 공백 금지. Variables 탭 아님!)
- `브리핑 발송 완료` → 발송은 됨. 텔레그램 앱 알림 설정/차단 확인
- `샘플 데이터 상태 - 발송 생략` → 수집이 한 번도 성공 못한 상태 → 2번 참고

실행이 **빨강**이고 로그에 `403 Forbidden` → 봇 대화방에서 Start를 안 누른 것.
텔레그램에서 내 봇 열고 시작 누른 뒤 재실행. `400 chat not found` → CHAT_ID 숫자 오타.

토큰 유출 의심 시: BotFather에서 `/revoke` → 새 토큰을 시크릿에 교체. 피해 범위는
"봇 명의 메시지 발송"까지이며 계정·자산 접근은 불가능.

## 4. 사이트가 안 열림 (404/빈 화면)

- 404 → Settings → Pages에서 Branch가 `main` + `/ (root)`인지 확인 후 Save
- 열리는데 "data.json을 불러오지 못했습니다" → data.json이 지워졌거나 손상
  → `history/` 폴더의 최신 날짜 JSON 내용을 data.json으로 복사하면 즉시 복구
- 옛날 화면이 계속 보임 → PWA 캐시. 새로고침 2회 또는 홈 화면 앱 삭제 후 재추가

## 5. GitHub 자체가 문제일 때 (정책 변경·계정 문제)

이 저장소가 곧 전체 백업이다: 코드 + 일별 히스토리 전부.
- 가벼운 보험: 가끔 `Code → Download ZIP`으로 내려받아 보관
- 이전이 필요하면 (무료 대안): **Cloudflare Pages**(호스팅+이메일 로그인 비공개 가능),
  cron은 cron-job.org 또는 Cloudflare Workers. AI에게:
  > "PLAYBOOK 5번. 이 저장소를 Cloudflare Pages + 외부 cron 구조로 이전하는
  > 마이그레이션을 만들어줘." — 코드 대부분 재사용 가능

## 6. AI에게 수리 맡기는 법 (모델 무관 공통 절차)

1. 새 세션에서 저장소를 연결하거나, 안 되면 `Code → Download ZIP`을 첨부
2. Actions의 에러 로그 몇 줄을 복사해서 함께 전달
3. "CLAUDE.md와 PLAYBOOK.md 읽고 시작해줘. 증상: ○○○" 라고 요청
4. AI가 준 수정 파일을 업로드하기 전, **테스트 통과했는지(37개+) 물어볼 것**
5. 반영 후 `Run workflow`로 즉시 검증

## 7. 정기 점검 (선택, 월 1회면 충분)

- Actions 최근 실행들이 초록인지 훑어보기
- 대시보드 "장 마감 기준" 날짜가 최신 영업일인지
- 성과 트래킹 승률 확인 → 필요하면 CONFIG 튜닝 (README 가이드)
