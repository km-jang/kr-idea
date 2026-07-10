# 국내장 아이디어 대시보드

수급(외국인·기관) + 밸류(PBR·PER·배당) + DART 공시 시그널을 매 영업일 자동 수집해
"오늘의 아이디어 5선"을 뽑아주는 웹 대시보드입니다.
아이폰·안드로이드·PC 어디서든 GitHub Pages 주소로 접속해 볼 수 있습니다.

- 데이터 소스: 네이버 증권(시세·수급·펀더멘털), DART 전자공시(RSS / OpenAPI)
- 갱신 주기: 매 영업일 19:10 KST (GitHub Actions, 수동 실행도 가능)
- 비용: 0원 (GitHub 무료 플랜으로 충분)

> ⚠️ 투자 참고 자료이며 매수·매도 추천이 아닙니다. 데이터 오류·지연이 있을 수 있습니다.

## 설치 (약 5분)

1. **저장소 만들기** — GitHub에서 새 저장소 생성 (예: `kr-idea-dashboard`, **Public**
   — 무료 플랜에서 Pages를 쓰려면 Public이어야 합니다)

2. **파일 업로드** — 이 폴더의 내용 전체를 저장소에 올립니다.
   - 웹에서: 저장소 → `Add file` → `Upload files` → 폴더 내용물 전체 드래그
     (`.github` 폴더가 누락되지 않게 주의 — 안 보이면 압축 해제 시 숨김 파일 표시를 켜세요.
     웹 업로드 시에는 `Add file` → `Create new file`로 `.github/workflows/update.yml` 경로를
     직접 만들어 내용을 붙여넣는 것이 확실합니다)
   - 또는 git 사용: `git init && git add -A && git commit -m init && git push`

3. **GitHub Pages 켜기** — 저장소 `Settings` → `Pages` →
   Source: `Deploy from a branch`, Branch: `main`, Folder: **`/docs`** → Save

4. **Actions 권한 확인** — `Settings` → `Actions` → `General` →
   Workflow permissions: **Read and write permissions** 선택 → Save

5. **첫 데이터 수집** — `Actions` 탭 → `데이터 갱신` → `Run workflow` 클릭
   (2~10분 소요. 완료되면 `docs/data.json`이 실제 데이터로 교체됩니다)

접속 주소: `https://<내아이디>.github.io/kr-idea-dashboard/`
휴대폰 홈 화면에 추가해두면 앱처럼 쓸 수 있습니다.

## 선택: DART OpenAPI 키 (권장)

기본은 DART 당일 RSS를 사용합니다. 무료 API 키를 넣으면 **최근 3일** 공시로 확장됩니다.

1. https://opendart.fss.or.kr 회원가입 → 인증키 신청 (즉시 발급, 무료)
2. 저장소 `Settings` → `Secrets and variables` → `Actions` → `New repository secret`
   - Name: `DART_API_KEY`, Value: 발급받은 키

## 구조

```
├── docs/
│   ├── index.html      # 대시보드 (정적 페이지, data.json을 읽어 렌더링)
│   └── data.json       # 수집 결과 (Actions가 매일 갱신; 초기엔 샘플 데이터)
├── scripts/collect.py  # 데이터 수집·점수화 파이프라인
├── tests/test_parsers.py  # 오프라인 테스트 (네트워크 불필요)
└── .github/workflows/update.yml  # 매 영업일 자동 실행
```

## 점수 산식 (기본값 — 취향대로 튜닝하세요)

| 항목 | 배점 | 내용 |
|---|---|---|
| 수급 | 40 | 외국인 연속 순매수일(최대 20) + 기관 연속(최대 10) + 동반매수(5) + 외인 5일 100억↑(5) |
| 밸류 | 40 | 저PBR 백분위(15) + 고배당 백분위(15) + PER 12배 미만(10) |
| 공시 | ±20 | 자사주 매입/소각·무상증자 등 호재 가점, 유상증자·CB 등 악재 감점 |

"오늘의 아이디어 5선"은 종합점수 상위 5개 (시총 3,000억 이상, 악재 공시 종목 제외).
`scripts/collect.py`의 `score_stocks()` / `pick_ideas()`에서 조정할 수 있습니다.

## 로컬 테스트

```bash
pip install -r requirements.txt
python tests/test_parsers.py          # 오프라인 테스트
python scripts/collect.py --sample    # 샘플 데이터 생성
cd docs && python -m http.server      # http://localhost:8000 에서 미리보기
```

주의: `collect.py` 전체 수집은 자유로운 네트워크 환경(집 PC, GitHub Actions)에서만 동작합니다.
