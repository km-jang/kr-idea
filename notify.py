#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""텔레그램 브리핑 발송 - data.json을 읽어 '오늘의 아이디어'를 텔레그램으로 보낸다.

필요 환경변수 (GitHub Secrets로 등록):
  TELEGRAM_BOT_TOKEN : @BotFather에서 발급받은 봇 토큰
  TELEGRAM_CHAT_ID   : 받을 사람의 chat id (@userinfobot으로 확인)

실행:
  python notify.py            # data.json 기준 브리핑 발송
  python notify.py --dry-run  # 발송 없이 메시지 내용만 출력 (테스트)
"""

import argparse
import html
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data.json"
WATCHLIST_PATH = ROOT / "watchlist.txt"
SITE_URL = "https://km-jang.github.io/kr-idea/"


def parse_watchlist(text):
    """watchlist.txt 내용 → 종목코드 리스트. '# 주석'과 빈 줄 무시, 6자리 숫자만 추출."""
    codes = []
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        m = __import__("re").search(r"\b(\d{6})\b", line)
        if m and m.group(1) not in codes:
            codes.append(m.group(1))
    return codes


def watchlist_lines(data, codes):
    """관심종목 현황 라인 생성 (data.json의 all_stocks 기준)."""
    if not codes:
        return []
    pool = {s["code"]: s for s in (data.get("all_stocks") or [])}
    if not pool:
        for s in (data.get("flow_scan") or []) + (data.get("ideas") or []):
            pool.setdefault(s["code"], s)
    out = []
    for c in codes[:15]:
        s = pool.get(c)
        if not s:
            continue
        chg = s.get("change_pct")
        sign = "" if chg is None else ("▲" if chg > 0 else ("▼" if chg < 0 else "-"))
        chg_s = "" if chg is None else f" {sign}{abs(chg):.1f}%"
        extra = ""
        if (s.get("f_streak") or 0) >= 3:
            extra = f" · 외인{s['f_streak']}일↑"
        out.append(f"· {s['name']} {fmt_num(s.get('price'), 0)}{chg_s}{extra}")
    return out


def fmt_num(v, d=2):
    if v is None:
        return "-"
    return f"{v:,.{d}f}".rstrip("0").rstrip(".") if d else f"{v:,.0f}"


def arrow(pct):
    if pct is None:
        return ""
    return "▲" if pct > 0 else ("▼" if pct < 0 else "-")


# ---------------------------------------------------------------------------
# 미국장 연동 (stooq.com 무료 데이터, 키 불필요)
# ---------------------------------------------------------------------------

US_INDICES = [
    ("^spx", "S&P500"), ("^ndq", "나스닥"), ("^sox", "반도체SOX"),
    ("usdkrw", "환율"), ("cl.f", "WTI"),
]
US_MAP_PATH = ROOT / "us_kr_map.json"


def fetch_stooq_change(symbol, days=10):
    """stooq 일봉 CSV → (최근종가, 등락률%). 실패 시 (None, None)."""
    try:
        import datetime as _dt
        end = _dt.date.today()
        start = end - _dt.timedelta(days=days)
        url = (f"https://stooq.com/q/d/l/?s={symbol}"
               f"&d1={start:%Y%m%d}&d2={end:%Y%m%d}&i=d")
        r = requests.get(url, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None, None
        return parse_stooq_csv(r.text)
    except Exception:
        return None, None


def parse_stooq_csv(text):
    """stooq CSV (Date,Open,High,Low,Close[,Volume]) → (마지막 종가, 등락률%)"""
    rows = [ln.split(",") for ln in (text or "").strip().splitlines()[1:] if "," in ln]
    closes = []
    for row in rows:
        try:
            closes.append(float(row[4]))
        except (IndexError, ValueError):
            continue
    if len(closes) < 2:
        return (closes[-1], None) if closes else (None, None)
    last, prev = closes[-1], closes[-2]
    return last, round((last / prev - 1) * 100, 2)


def us_mood_line(chg_map):
    """규칙 기반 시장 분위기 워딩 (API 불필요)."""
    ndq = chg_map.get("나스닥")
    sox = chg_map.get("반도체SOX")
    fx = chg_map.get("환율")
    lines = []
    if ndq is not None:
        if ndq >= 1.5:
            lines.append("미국 기술주 강세 마감 — 성장주 우호적 출발 기대")
        elif ndq <= -1.5:
            lines.append("미국 기술주 약세 마감 — 보수적 접근 권장")
    if sox is not None and abs(sox) >= 2 and (ndq is None or abs(sox) > abs(ndq)):
        lines.append("반도체지수 변동 큼 — 반도체 대형주 갭 주의"
                     if sox < 0 else "반도체지수 강세 — 반도체 대형주 주목")
    if fx is not None and fx >= 0.5:
        lines.append("환율 상승(원화 약세) — 외국인 수급에 부담 가능")
    elif fx is not None and fx <= -0.5:
        lines.append("환율 하락(원화 강세) — 외국인 수급 우호적")
    return " · ".join(lines[:2])


def load_us_map():
    try:
        return json.loads(US_MAP_PATH.read_text(encoding="utf-8")).get("mappings", [])
    except Exception:
        return []


def gap_signal_lines(fetch=None, threshold=3.0):
    """연동주 매핑: 미국 종목 ±threshold% 이상이면 국내 관련주 라인 생성."""
    fetch = fetch or fetch_stooq_change
    out = []
    for m in load_us_map()[:10]:
        _, chg = fetch(m.get("us", ""))
        if chg is None or abs(chg) < threshold:
            continue
        sign = "▲" if chg > 0 else "▼"
        mood = "주목" if chg > 0 else "약세 주의"
        krs = "·".join(m.get("kr", [])[:3])
        out.append(f"⚡ {m.get('us_name')} {sign}{abs(chg):.1f}% → "
                   f"{m.get('theme')} ({krs}) {mood}")
    return out[:4]


def us_market_block():
    """아침 브리핑용 미국장 블록. 어떤 실패에도 빈 리스트 반환 (브리핑 발송은 계속)."""
    try:
        chg_map, parts = {}, []
        for sym, name in US_INDICES:
            val, chg = fetch_stooq_change(sym)
            if val is None:
                continue
            chg_map[name] = chg
            if name == "환율":
                arrow_s = "" if chg is None else ("▲" if chg > 0 else "▼")
                parts.append(f"환율 {val:,.0f}원{arrow_s}")
            elif name == "WTI":
                parts.append(f"WTI {val:,.1f}")
            else:
                arrow_s = "" if chg is None else ("▲" if chg > 0 else "▼")
                chg_s = "" if chg is None else f"{arrow_s}{abs(chg):.1f}%"
                parts.append(f"{name} {chg_s}")
        if not parts:
            return []
        lines = ["🌎 <b>밤사이 미국장</b>", " · ".join(parts)]
        mood = us_mood_line(chg_map)
        if mood:
            lines.append(f"<i>{mood}</i>")
        lines.extend(gap_signal_lines())
        lines.append("")
        return lines
    except Exception:
        return []


def build_message(data):
    """data.json → 텔레그램 메시지 (HTML 포맷)."""
    e = lambda s: html.escape(str(s or ""))
    md = (data.get("market_date") or "").replace("-", ".")
    lines = [f"📊 <b>국내장 아이디어 브리핑</b>  <i>({e(md)} 장 마감 기준)</i>", ""]

    lines.extend(us_market_block())   # 🌎 밤사이 미국장 (실패 시 자동 생략)

    idx = data.get("indices") or {}
    k, q = idx.get("KOSPI") or {}, idx.get("KOSDAQ") or {}
    if k.get("value"):
        lines.append(
            f"KOSPI {fmt_num(k['value'])} {arrow(k.get('change_pct'))}"
            f"{abs(k.get('change_pct') or 0):.2f}%"
            f" · KOSDAQ {fmt_num(q.get('value'))} {arrow(q.get('change_pct'))}"
            f"{abs(q.get('change_pct') or 0):.2f}%")
        lines.append("")

    ideas = data.get("ideas") or []
    if ideas:
        lines.append("<b>오늘의 아이디어 5선</b>")
        for i, s in enumerate(ideas, 1):
            reasons = " · ".join(s.get("reasons", [])[:2]) or "-"
            lines.append(f"{i}. <b>{e(s['name'])}</b> ({s.get('score')}점)")
            lines.append(f"   {e(reasons)}")
    else:
        lines.append("오늘은 조건을 만족하는 종목이 없습니다.")
    lines.append("")

    pos = [d for d in (data.get("disclosures") or []) if d.get("sentiment") == "positive"]
    if pos:
        head = ", ".join(f"{e(d['company'])}({e(d['tag'])})" for d in pos[:4])
        more = f" 외 {len(pos)-4}건" if len(pos) > 4 else ""
        lines.append(f"🟢 호재성 공시: {head}{more}")

    neg = [d for d in (data.get("disclosures") or []) if d.get("sentiment") == "negative"]
    if neg:
        head = ", ".join(f"{e(d['company'])}({e(d['tag'])})" for d in neg[:3])
        more = f" 외 {len(neg)-3}건" if len(neg) > 3 else ""
        lines.append(f"🔴 악재성 공시: {head}{more}")

    if pos or neg:
        lines.append("")

    wl = watchlist_lines(data, parse_watchlist(
        WATCHLIST_PATH.read_text(encoding="utf-8") if WATCHLIST_PATH.exists() else ""))
    if wl:
        lines.append("<b>⭐ 내 관심종목</b>")
        lines.extend(e(x) for x in wl)
        lines.append("")

    lines.append(f'📈 <a href="{SITE_URL}">대시보드 전체 보기</a>')
    lines.append("<i>투자 참고 자료이며 매수·매도 추천이 아닙니다.</i>")
    return "\n".join(lines)


def build_evening_message(data):
    """저녁 마감 요약 - 짧은 버전."""
    e = lambda s: html.escape(str(s or ""))
    md = (data.get("market_date") or "").replace("-", ".")
    idx = data.get("indices") or {}
    k, q = idx.get("KOSPI") or {}, idx.get("KOSDAQ") or {}
    lines = [f"🌙 <b>마감 요약</b>  <i>({e(md)})</i>", ""]
    if k.get("value"):
        lines.append(
            f"KOSPI {fmt_num(k['value'])} {arrow(k.get('change_pct'))}"
            f"{abs(k.get('change_pct') or 0):.2f}%"
            f" · KOSDAQ {fmt_num(q.get('value'))} {arrow(q.get('change_pct'))}"
            f"{abs(q.get('change_pct') or 0):.2f}%")
    ideas = data.get("ideas") or []
    if ideas:
        names = " · ".join(
            f"{e(s['name'])}{' 🆕' if s.get('idea_days') == 1 else ''}" for s in ideas)
        lines.append(f"오늘의 5선: {names}")
    wl = watchlist_lines(data, parse_watchlist(
        WATCHLIST_PATH.read_text(encoding="utf-8") if WATCHLIST_PATH.exists() else ""))
    if wl:
        lines.append("")
        lines.append("<b>⭐ 내 관심종목</b>")
        lines.extend(e(x) for x in wl)
    lines.append("")
    lines.append(f'상세는 내일 아침 8시 브리핑 또는 <a href="{SITE_URL}">대시보드</a>에서.')
    return "\n".join(lines)


def build_weekly_message(data):
    """일요일 저녁 주간 결산 - 성과 트래킹 기반."""
    e = lambda s: html.escape(str(s or ""))
    lines = ["📅 <b>주간 결산</b>", ""]

    trend = data.get("kospi_trend") or []
    week = trend[-5:] if len(trend) >= 2 else []
    if len(week) >= 2 and week[0].get("v"):
        chg = (week[-1]["v"] / week[0]["v"] - 1) * 100
        sign = "▲" if chg > 0 else ("▼" if chg < 0 else "-")
        lines.append(f"KOSPI 주간 {sign}{abs(chg):.2f}%  "
                     f"({fmt_num(week[0]['v'])} → {fmt_num(week[-1]['v'])})")
        lines.append("")

    p = data.get("performance") or {}
    s = p.get("summary") or {}
    if p.get("days"):
        lines.append("<b>아이디어 5선 성적표</b>")
        avg = s.get("avg_ret_pct")
        lines.append(f"· 평균 수익률(선정일→현재): "
                     f"{'+' if (avg or 0) > 0 else ''}{avg}%")
        lines.append(f"· 승률: {s.get('win_rate_pct')}%  ·  "
                     f"KOSPI 대비 우위: {s.get('beat_kospi_pct')}%  ·  "
                     f"추적 {p['days']}일")
        recs = p.get("records") or []
        if recs:
            best = max(recs, key=lambda r: r["avg_ret_pct"])
            worst = min(recs, key=lambda r: r["avg_ret_pct"])
            lines.append(f"· 최고의 날: {e(best['date'][5:])} "
                         f"(+{best['avg_ret_pct']}%) / 아쉬운 날: {e(worst['date'][5:])} "
                         f"({worst['avg_ret_pct']}%)")
            # 이번 주 최다 선정 종목
            cnt = {}
            for r in recs[:5]:
                for it in r.get("ideas", []):
                    cnt[it["name"]] = cnt.get(it["name"], 0) + 1
            top = sorted(cnt.items(), key=lambda x: -x[1])[:3]
            if top:
                lines.append("· 최다 선정: " +
                             ", ".join(f"{e(n)}({c}회)" for n, c in top))
    else:
        lines.append("아직 성과 데이터가 쌓이는 중입니다. 다음 주부터 성적표가 나옵니다.")
    lines.append("")
    lines.append(f'📈 <a href="{SITE_URL}">대시보드에서 상세 보기</a>')
    lines.append("<i>투자 참고 자료이며 매수·매도 추천이 아닙니다.</i>")
    return "\n".join(lines)


def send(token, chat_id, text):
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20)
    ok = r.status_code == 200 and r.json().get("ok")
    if not ok:
        print(f"텔레그램 발송 실패: {r.status_code} {r.text[:300]}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="발송 없이 내용만 출력")
    ap.add_argument("--evening", action="store_true", help="저녁 마감 요약 (짧은 버전)")
    ap.add_argument("--weekly", action="store_true", help="일요일 주간 결산")
    ap.add_argument("--data", default=str(DATA_PATH))
    args = ap.parse_args()

    try:
        data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"data.json 읽기 실패: {exc}")
        sys.exit(1)

    if data.get("sample"):
        print("샘플 데이터 상태 - 발송 생략 (첫 수집 후 발송됩니다)")
        return

    msg = (build_weekly_message(data) if args.weekly
           else build_evening_message(data) if args.evening
           else build_message(data))

    if args.dry_run:
        print(msg)
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 시크릿이 없어 발송 생략")
        return

    if send(token, chat_id, msg):
        print("브리핑 발송 완료")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
