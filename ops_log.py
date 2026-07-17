"""실행 성적표 장부 (ops.json) — 야간 자가 점검이 하루 1회 그날의 자동화 결과를 기록.

기록 항목: 마감 수집 반영 여부, 아침/점심/저녁 발송, 종가 스캔, 자가복구 여부.
대시보드 자가진단(신호등 클릭)에서 최근 5영업일 매트릭스로 표시된다.
사용: python ops_log.py [none|ok|fail]   (인자 = 그날 자가복구 결과)
"""
import json
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
KEEP_DAYS = 30


def build_record(data, sent, scans, today, recover="none"):
    """당일 실행 결과 1건을 조립한다 (모든 입력은 이미 파싱된 객체, 오프라인 테스트 가능)."""
    gen = str((data or {}).get("generated_at") or "")
    return {
        "date": today,
        "data_final": bool(gen[:10] == today and gen[11:16] >= "15:40"),
        "data_gen": gen,
        "morning": (sent or {}).get("morning") == today,
        "pulse": (sent or {}).get("pulse") == today,
        "evening": (sent or {}).get("evening") == today,
        "scan": any(r.get("date") == today for r in (scans or []) if isinstance(r, dict)),
        "recover": recover,  # none=복구 불필요 | ok=자가복구 성공 | fail=복구 실패
    }


def append_record(log, record, keep=KEEP_DAYS):
    """장부에 기록 추가 (같은 날짜는 최신으로 교체, keep일만 유지). 순수 함수."""
    if not isinstance(log, list):
        log = []
    log = [r for r in log if isinstance(r, dict) and r.get("date") != record["date"]]
    log.append(record)
    return sorted(log, key=lambda r: r.get("date", ""))[-keep:]


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def main():
    recover = sys.argv[1] if len(sys.argv) > 1 else "none"
    if recover not in ("none", "ok", "fail"):
        recover = "none"
    today = datetime.now(KST).strftime("%Y-%m-%d")
    record = build_record(
        _load("data.json", {}), _load("sent_log.json", {}),
        _load("scans.json", []), today, recover)
    log = append_record(_load("ops.json", []), record)
    with open("ops.json", "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)
    print(f"ops 기록 완료: {record}")


if __name__ == "__main__":
    main()
