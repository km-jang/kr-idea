# 컴퓨터 없이 작업하고 올리는 방법 계획 (2026-11-02)

소유자 요청 2026-11-02: PC가 꺼져 있어도 작업하고 업데이트하고 싶다. 이 문서는 계획이며 승인 전에는 파일을 만들지 않는다. 클라우드 작업실의 동작은 이 세션에서 직접 확인하지 못했다(확인 못 함).

## 1. 방식

인터넷 작업실(claude.ai/code)에서 Claude가 고치고 시험을 돌려 `claude/` 가지에 올리고 PR(병합 신청서)을 만든다. 소유자가 휴대폰에서 PR을 열어 `Merge`를 누르면 main에 반영된다. 병합 버튼이 곧 올리기 승인이다.

흐름
1. 휴대폰 claude.ai/code에서 새 작업, 저장소 km-jang/kr-idea와 km-jang/pjt-fnj 연결
2. 요청 입력, Claude가 수정과 시험
3. PR 링크 확인, 시험 표시가 초록이면 `Merge pull request`
4. 반영 확인은 Claude에게 요청

## 2. 준비물 3가지

### 2.1 PR 시험 검사 (`.github/workflows/pr-check.yml` 신설)

지금 자동 시험은 main에 올라간 뒤(smoke.yml)나 수집 직전(update.yml)에 돈다. PR 단계에는 없다. 아래 파일을 추가하면 PR마다 문법 검사와 단위 시험이 돌고, 실패하면 빨간 표시가 뜬다. smoke.yml 머리말대로 update.yml에는 합치지 않고 별도 파일로 둔다. 읽기 권한만 쓰고 비밀값은 쓰지 않는다.

```yaml
name: PR 시험

on:
  pull_request:
    branches: [main]
    paths:
      - '**.py'
      - 'requirements.txt'
      - '*.html'
      - '.github/workflows/*.yml'
  workflow_dispatch: {}

permissions:
  contents: read

concurrency:
  group: pr-check-${{ github.ref }}
  cancel-in-progress: true

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v6

      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"

      - name: 0) 문법 검사 (전체 .py)
        shell: bash
        run: python -m compileall -q .

      - name: 의존성 설치
        run: pip install -r requirements.txt

      - name: 1) 단위 테스트
        shell: bash
        run: python test_parsers.py
```

* 올리는 방법은 웹 편집기 붙여넣기다(`.github/` 파일은 끌어놓기가 안 된다). 새 파일이라 `Add file`의 `Create new file`에서 이름에 `.github/workflows/pr-check.yml`을 쓰고 위 내용을 붙여넣는다.
* 올린 뒤 PR 보호 규칙(시험 통과해야 병합 버튼 활성)은 켜지 않는다. 빨간 표시를 보고 소유자가 판단한다. 규칙까지 켜면 시험 파일이 깨졌을 때 고칠 PR도 막힌다.
* 처음 한 번은 작은 PR로 시험해 초록과 빨강 표시가 둘 다 뜨는지 본다.

### 2.2 kr-idea 폴더 CLAUDE.md에 넣을 문단 초안

> ## 클라우드 세션 (PC 없이 작업, 2026-11-02 신설)
>
> claude.ai/code 세션은 PC 메모리와 `.claude\handover`, `_업로드대기` 폴더를 볼 수 없다. 기준 인계 문서는 저장소의 `HANDOFF.md` 하나다. 코드는 `claude/` 가지에서 고치고 시험(`python test_parsers.py`)을 돌린 뒤 PR을 만든다. main에 직접 올리지 않는다. 올리기는 소유자가 PR의 `Merge`를 누르는 것이다. 코드 파일 전체 교체 원칙과 이 파일의 절대 규칙은 PC 세션과 같다. 네이버 접속이나 Actions 로그 읽기가 막히면 막힌 것을 보고하고 그 점검은 PC 세션 몫으로 넘긴다.

### 2.3 저장소 두 개 연결

통합 작업 지침(한국어 규칙, 안전 규칙)은 km-jang/pjt-fnj에 있다. 클라우드 세션을 열 때 두 저장소를 같이 연결한다. 4절 웹 세션 규칙대로 훅 알림이 없을 수 있으므로 첫 답에서 이름·턴·사용률 점검을 스스로 한다.

## 3. 결정 기록 대조

HANDOFF 10절 "배포는 업로드 방식 유지"와 충돌하지 않는다. PR 병합은 소유자 승인 뒤에만 반영되는 방식이고 main 직접 올리기는 쓰지 않는다. 12절에 대기 중인 "웹 세션 직접 올리기를 예외로 적을지"는 이 계획에서 쓰지 않으므로 그대로 둔다. 유료 서비스와 새 외부 요청은 없다.

## 4. 확인해야 할 것 (첫 클라우드 세션에서 직접 시험)

1. 작업실에서 네이버·DART 접속과 `python test_parsers.py` 실행이 되는지
2. 두 저장소 동시 연결이 되는지
3. PR 시험 표시가 휴대폰 GitHub 화면에서 보이는지
4. 봇이 올리는 data.json·history와 PR이 같은 파일을 건드려 충돌하는지(코드 파일만 바뀌는 PR이면 안 날 것으로 보나 확인 못 함)

안 되는 항목은 그 작업만 PC 세션에 남긴다.

## 5. 하지 않는 것

* main 직접 올리기와 PR 자동 병합은 쓰지 않는다.
* 휴대폰 GitHub 화면에서 코드를 직접 고치지 않는다.
* 비밀값을 작업실에 넣지 않는다. 비밀값이 필요한 작업(텔레그램 발송 시험 등)은 PC나 Actions 몫이다.

## 6. 소유자 결정 대기

1. 2.1 시험 검사 파일을 이 세션이 `_업로드대기`에 만들어 두는 것에 동의하는지
2. 2.2 문단을 kr-idea CLAUDE.md에 넣는 것에 동의하는지
3. 첫 클라우드 세션 시험(4절)은 소유자가 휴대폰에서 직접 열어 본다
