# 국내장 아이디어 대시보드

수급(외국인·기관) + 밸류(PBR·PER·배당) + DART 공시 시그널을 매 영업일 자동 수집해
"오늘의 아이디어 5선"을 뽑아주는 웹 대시보드.

- 접속: https://km-jang.github.io/kr-idea/
- 갱신: 매 영업일 19:10 KST 자동 (GitHub Actions) + Actions 탭에서 수동 실행 가능
- 데이터: 네이버 증권(시세·수급·펀더멘털), DART 전자공시

> ⚠️ 투자 참고 자료이며 매수·매도 추천이 아닙니다. 데이터 오류·지연이 있을 수 있습니다.

## 파일 구성 (전부 최상위)

| 파일 | 역할 |
|---|---|
| `index.html` | 대시보드 웹페이지 |
| `data.json` | 수집 결과 (Actions가 매일 갱신) |
| `collect.py` | 데이터 수집·점수화 파이프라인 |
| `test_parsers.py` | 오프라인 테스트 |
| `requirements.txt` | 파이썬 의존성 |
| `.github/workflows/update.yml` | 자동 실행 스케줄 |

## 설정 (1회)

1. Settings → Pages → Source: `Deploy from a branch`, Branch: `main` / `/ (root)` → Save
2. Settings → Actions → General → Workflow permissions: `Read and write permissions` → Save
3. Actions 탭 → `데이터 갱신` → `Run workflow` (첫 데이터 수집)

### 선택: DART OpenAPI 키 (공시 범위 당일 → 3일)

https://opendart.fss.or.kr 에서 무료 키 발급 후
Settings → Secrets and variables → Actions → New repository secret →
Name: `DART_API_KEY`, Value: 발급 키

## 점수 산식

수급(40) = 외국인 연속 순매수(최대 20) + 기관 연속(최대 10) + 동반매수(5) + 외인 5일 100억↑(5)
밸류(40) = 저PBR 백분위(15) + 고배당 백분위(15) + PER 12배 미만(10)
공시(±20) = 자사주·소각·무상증자 가점 / 유상증자·CB 감점

"오늘의 아이디어 5선" = 종합점수 상위 5 (시총 3,000억 이상, 악재 공시 제외).
`collect.py`의 `score_stocks()` / `pick_ideas()`에서 튜닝.
