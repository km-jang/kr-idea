#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""종가매매 후보 스캐너 - 장 마감 전(14:50 KST) 실행.

data.json의 유니버스를 실시간 시세로 스캔해 '종가 부근까지 강한 종목'을
선별하고 텔레그램으로 발송한다.

시그널 (CONFIG_SCAN에서 튜닝):
  1) 당일 등락률 +3% ~ +15% (과열 상한가권 제외)
  2) 현재가가 당일 고가의 98% 이상 (막판까지 안 밀림)
  3) 당일 거래대금이 전일 대비 급증 (기본 2.5배 이상)
  4) 시총·거래대금 하한 필터
  가점) 외국인 연속 순매수(전일까지) / 52주 신고가 근접

실행:
  python closing_scan.py            # 스캔 + 텔레그램 발송
  python closing_scan.py --dry-run  # 발송 없이 결과 출력
"""

import argparse
import html
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data.json"
SITE_URL = "https://km-jang.github.io/kr-idea/"

# --- 튜닝 파라미터 --------------------------------------------------------
CONFIG_SCAN = {
    "chg_min": 3.0,          # 최소 등락률 (%)
    "chg_max": 15.0,         # 최대 등락률 (%) - 그 이상은 과열로 제외
    "near_high": 0.98,       # 현재가 / 당일고가 최소 비율
    "turnover_mult": 2.5,    # 거래대금 전일 대비 최소 배수
    "min_mktcap": 1000,      # 최소 시총 (억)
    "min_turnover": 50,      # 최소 당일 거래대금 (억)
    "top_n": 5,              # 후보 개수
}

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def fetch_realtime(codes, chunk=20):
    """네이버 실시간 API - 여러 종목 묶음 조회 → {코드: {price, high, volume, chg}}"""
    out = {}
    for i in range(0, len(codes), chunk):
        batch = ",".join(codes[i:i + chunk])
        url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{batch}"
        try:
            r = requests.get(url, timeout=12, headers=UA)
            if r.status_code != 200:
                continue
            out.update(parse_realtime(r.json()))
        except Exception:
            continue
        time.sleep(0.1)
    return out


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_realtime(data):
    """실시간 API 응답 → {코드: 시세 dict}"""
    out = {}
    for d in (data or {}).get("datas") or []:
        code = str(d.get("itemCode") or "").zfill(6)
        if not code.strip("0"):
            continue
        chg = _num(d.get("fluctuationsRatio"))
        crt = str((d.get("compareToPreviousPrice") or {}).get("code", ""))
        if chg is not None and chg > 0 and crt in ("5", "4"):
            chg = -chg
        out[code] = {
            "price": _num(d.get("closePrice")),
            "high": _num(d.get("highPrice")),
            "volume": _num(d.get("accumulatedTradingVolume")),
            "chg": chg,
        }
    return out


def scan_candidates(universe, quotes, cfg=None):
    """유니버스(전일 데이터) x 실시간 시세 → 종가매매 후보 목록 (점수순)."""
    cfg = cfg or CONFIG_SCAN
    cands = []
    for s in universe:
        q = quotes.get(s.get("code"))
        if not q or not q.get("price"):
            continue
        chg, price, high = q.get("chg"), q["price"], q.get("high")
        if chg is None or not (cfg["chg_min"] <= chg <= cfg["chg_max"]):
            continue
        if not high or price / high < cfg["near_high"]:
            continue
        if (s.get("mktcap_100m") or 0) < cfg["min_mktcap"]:
            continue
        to_today = price * (q.get("volume") or 0) / 1e8
        if to_today < cfg["min_turnover"]:
            continue
        prev_to = (s.get("price") or 0) * (s.get("volume") or 0) / 1e8
        mult = round(to_today / prev_to, 1) if prev_to > 20 else None
        if mult is not None and mult < cfg["turnover_mult"]:
            continue
        # 점수: 등락률 강도 + 고가유지 + 거래대금 배수 + 보조(수급/신고가)
        pts = chg + (price / high) * 10 + min(mult or 0, 10)
        notes = [f"+{chg:.1f}%", f"고가유지 {price/high*100:.0f}%"]
        if mult:
            notes.append(f"대금 {mult}배")
        if (s.get("f_streak") or 0) >= 3:
            pts += 3
            notes.append(f"외인 {s['f_streak']}일 연속")
        if (s.get("near_52w_pct") or 0) >= 90:
            pts += 2
            notes.append("52주 고점권")
        cands.append({"code": s["code"], "name": s["name"], "price": price,
                      "chg": chg, "score": round(pts, 1), "notes": notes})
    cands.sort(key=lambda c: -c["score"])
    return cands[:cfg["top_n"]]


def build_scan_message(cands, now=None):
    e = html.escape
    now = now or datetime.now(KST)
    lines = [f"🔔 <b>종가매매 후보 스캔</b>  <i>({now:%H:%M} 기준)</i>", ""]
    if not cands:
        lines.append("오늘은 조건을 만족하는 종목이 없습니다. (무리한 진입 금지 신호로 해석)")
    else:
        for i, c in enumerate(cands, 1):
            lines.append(f"{i}. <b>{e(c['name'])}</b> {c['price']:,.0f}원")
            lines.append(f"   {e(' · '.join(c['notes']))}")
    lines.append("")
    lines.append(f'📈 <a href="{SITE_URL}">대시보드</a>')
    lines.append("<i>단기 트레이딩은 위험이 큽니다. 스크리닝 결과일 뿐 매수 신호가 아닙니다.</i>")
    return "\n".join(lines)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("텔레그램 시크릿 없음 - 발송 생략")
        return True
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=20)
    ok = r.status_code == 200 and r.json().get("ok")
    print("발송 완료" if ok else f"발송 실패: {r.status_code} {r.text[:200]}")
    return ok


def build_pulse_message(data, quotes, now=None):
    """☀️ 장중 시황 요약 (12시대 점심 맥박) - 저장 없이 메시지만."""
    now = now or datetime.now(KST)
    lines = [f"☀️ <b>장중 시황</b>  <i>({now.strftime('%H:%M')})</i>", ""]
    # 지수 (실시간 API의 지수 코드 시도, 실패하면 생략)
    try:
        idx = fetch_realtime(["KOSPI", "KOSDAQ"])
        parts = []
        for name in ("KOSPI", "KOSDAQ"):
            q = idx.get(name)
            if q and q.get("chg") is not None:
                arrow = "▲" if q["chg"] > 0 else ("▼" if q["chg"] < 0 else "-")
                parts.append(f"{name} {arrow}{abs(q['chg']):.2f}%")
        if parts:
            lines.append(" · ".join(parts))
            lines.append("")
    except Exception:
        pass
    # 오늘 5선 장중 성적
    ideas = data.get("ideas") or []
    perf = []
    for s in ideas:
        q = quotes.get(s.get("code"))
        if q and q.get("chg") is not None:
            perf.append((s["name"], q["chg"]))
    if perf:
        avg = sum(p[1] for p in perf) / len(perf)
        head = " · ".join(f"{n} {'+' if c > 0 else ''}{c:.1f}%" for n, c in perf[:5])
        lines.append(f"5선 장중 평균 {'+' if avg > 0 else ''}{avg:.1f}%")
        lines.append(f"  {head}")
        lines.append("")
    # 장중 급등 (유니버스 기준 상위)
    universe = {s["code"]: s for s in data.get("all_stocks") or []}
    movers = sorted(
        ((universe[c]["name"], q["chg"]) for c, q in quotes.items()
         if c in universe and q.get("chg") is not None and q["chg"] >= 5),
        key=lambda x: -x[1])[:3]
    if movers:
        lines.append("🚀 장중 급등: " +
                     " · ".join(f"{n} +{c:.1f}%" for n, c in movers))
    # 관심종목 특이 (±3% 이상)
    try:
        sys.path.insert(0, str(ROOT))
        from notify import parse_watchlist
        watch = parse_watchlist()
    except Exception:
        watch = []
    wl = []
    for code in watch:
        q = quotes.get(code)
        if q and q.get("chg") is not None and abs(q["chg"]) >= 3:
            nm = universe.get(code, {}).get("name", code)
            wl.append(f"{nm} {'+' if q['chg'] > 0 else ''}{q['chg']:.1f}%")
    if wl:
        lines.append("⭐ 관심종목 특이: " + " · ".join(wl[:4]))
    lines.append("")
    lines.append("<i>장중 참고용 · 마감 집계는 저녁 요약에서</i>")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pulse", action="store_true", help="장중 시황 요약만 발송 (기록 없음)")
    ap.add_argument("--if-not-sent", action="store_true",
                    help="오늘 이미 발송했으면 조용히 종료 (예비 알람용)")
    ap.add_argument("--data", default=str(DATA_PATH))
    args = ap.parse_args()

    try:
        data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"data.json 읽기 실패: {exc}")
        sys.exit(1)
    if data.get("sample"):
        print("샘플 데이터 상태 - 스캔 생략")
        return
    universe = data.get("all_stocks") or []
    if not universe:
        print("유니버스 없음 (구버전 data.json) - 다음 수집 후 활성화")
        return

    print(f"[1/2] 실시간 시세 조회 ({len(universe)}종목)...")
    quotes = fetch_realtime([s["code"] for s in universe])
    print(f"  → {len(quotes)}종목 수신")
    if len(quotes) < len(universe) * 0.5:
        print("실시간 수신율 저조 - 발송 생략 (장중이 아닐 수 있음)")
        return

    if args.pulse:
        try:
            sys.path.insert(0, str(ROOT))
            import notify
        except Exception:
            notify = None
        if args.if_not_sent and notify and notify.already_sent("pulse"):
            print("오늘 점심 맥박 발송 기록 있음 - 예비 조용히 종료")
            return
        msg = build_pulse_message(data, quotes)
        print("[2/2] 장중 시황 요약")
        if args.dry_run:
            print(msg)
            return
        if not send_telegram(msg):
            sys.exit(1)
        if notify:
            notify.mark_sent("pulse")
        return

    cands = scan_candidates(universe, quotes)
    msg = build_scan_message(cands)
    print(f"[2/2] 후보 {len(cands)}건")
    if args.dry_run:
        print(msg)
        return
    save_scan_record(cands)               # 다음날 성적 채점용 기록
    if not send_telegram(msg):
        sys.exit(1)


SCANS_PATH = ROOT / "scans.json"


def save_scan_record(cands, path=None, keep=30):
    """스캔 결과를 scans.json에 누적 저장 (성과 검증 루프의 재료)."""
    path = Path(path or SCANS_PATH)
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            records = []
    except Exception:
        records = []
    now = datetime.now(KST)
    records = [r for r in records if r.get("date") != now.strftime("%Y-%m-%d")]
    records.append({
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "candidates": [{"code": c["code"], "name": c["name"],
                        "price": c["price"], "chg": c["chg"]} for c in cands],
    })
    path.write_text(json.dumps(records[-keep:], ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"스캔 기록 저장: {len(cands)}건")


if __name__ == "__main__":
    main()
