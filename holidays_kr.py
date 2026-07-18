"""한국거래소(KRX) 휴장일 달력 — 규칙 기반, 비용 0원.

용도: 휴장일에 수집·스캔·알림이 스스로 조용히 쉬게 하고,
아침 브리핑에는 "오늘 휴장" 안내를 붙인다.

유지보수: 매년 12월에 다음 해 목록을 KRX 공지 기준으로 추가할 것.
목록에 없는 해라도 시스템은 기존 방식(최근거래일 자동 감지)으로 동작하므로 깨지지 않는다 —
이 달력은 '더 조용하고 친절하게' 만드는 보강 장치다.

CLI: `python holidays_kr.py [YYYY-MM-DD]`
  → 휴장이면 사유를 출력(예: "제헌절"), 거래일이면 아무것도 출력하지 않는다.
"""
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# 주중 휴장일만 기록 (주말은 코드가 자동 판정). 출처: KRX 2026 휴장일정 + 제헌절 재지정(2026 시행).
KRX_CLOSED = {
    "2026-01-01": "신정",
    "2026-02-16": "설날 연휴",
    "2026-02-17": "설날",
    "2026-02-18": "설날 연휴",
    "2026-03-02": "삼일절 대체공휴일",
    "2026-05-01": "근로자의 날",
    "2026-05-05": "어린이날",
    "2026-05-25": "부처님오신날 대체공휴일",
    "2026-07-17": "제헌절",            # 2026년부터 공휴일 재지정 (18년 만)
    "2026-08-17": "광복절 대체공휴일",
    "2026-09-24": "추석 연휴",
    "2026-09-25": "추석",
    "2026-10-05": "개천절 대체공휴일",
    "2026-10-09": "한글날",
    "2026-12-25": "성탄절",
    "2026-12-31": "연말 휴장",
}


def closed_reason(date_str=None):
    """휴장 사유 반환 (거래일이면 None). date_str 없으면 오늘(KST)."""
    if not date_str:
        date_str = datetime.now(KST).strftime("%Y-%m-%d")
    if date_str in KRX_CLOSED:
        return KRX_CLOSED[date_str]
    try:
        dow = datetime.strptime(date_str, "%Y-%m-%d").weekday()
    except ValueError:
        return None
    if dow >= 5:
        return "주말"
    return None


def is_trading_day(date_str=None):
    return closed_reason(date_str) is None


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    reason = closed_reason(arg)
    if reason:
        print(reason)
