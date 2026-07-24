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
import time
from datetime import datetime, timedelta
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


# stooq 실패 시 야후 파이낸스로 자동 전환하기 위한 심볼 대응표
YAHOO_MAP = {
    "^spx": "^GSPC", "^ndq": "^IXIC", "^sox": "^SOX",
    "usdkrw": "KRW=X", "cl.f": "CL=F",
    "nvda.us": "NVDA", "mu.us": "MU", "amd.us": "AMD", "tsla.us": "TSLA",
    "aapl.us": "AAPL", "lly.us": "LLY", "avgo.us": "AVGO",
}


def fetch_stooq_change(symbol, days=10):
    """미국 시세: stooq 우선, 실패 시 야후 폴백 → (최근종가, 등락률%)."""
    val, chg = _stooq(symbol, days)
    if val is not None:
        return val, chg
    return _yahoo(YAHOO_MAP.get(symbol, symbol))


def _stooq(symbol, days=10):
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


def _yahoo(symbol):
    """야후 차트 API (키 불필요) → (최근종가, 등락률%)."""
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
               f"?range=5d&interval=1d")
        r = requests.get(url, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None, None
        return parse_yahoo_chart(r.json())
    except Exception:
        return None, None


def parse_yahoo_chart(data):
    """야후 v8 chart 응답 → (마지막 종가, 등락률%)"""
    try:
        res = data["chart"]["result"][0]
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
        if len(closes) >= 2:
            return round(closes[-1], 2), round((closes[-1] / closes[-2] - 1) * 100, 2)
        if closes:
            return round(closes[-1], 2), None
    except (KeyError, IndexError, TypeError):
        pass
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
            lines.append("미국 기술주 강세 마감 · 성장주 우호적 출발 기대")
        elif ndq <= -1.5:
            lines.append("미국 기술주 약세 마감 · 보수적 접근 권장")
    if sox is not None and abs(sox) >= 2 and (ndq is None or abs(sox) > abs(ndq)):
        lines.append("반도체지수 변동 큼 · 반도체 대형주 갭 주의"
                     if sox < 0 else "반도체지수 강세 · 반도체 대형주 주목")
    if fx is not None and fx >= 0.5:
        lines.append("환율 상승(원화 약세) · 외국인 수급에 부담 가능")
    elif fx is not None and fx <= -0.5:
        lines.append("환율 하락(원화 강세) · 외국인 수급 우호적")
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
        chg_map, parts, misses = {}, [], []
        for sym, name in US_INDICES:
            val, chg = fetch_stooq_change(sym)
            if val is None:
                misses.append(name)
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
        print(f"미국장 데이터: {len(parts)}/{len(US_INDICES)} 수신"
              + (f" (실패: {', '.join(misses)})" if misses else ""))
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
    lines.extend(compass_lines(data))  # 🧭 뉴스 나침반 (테마 점화·데뷔)

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

    it = data.get("insider_trades") or []
    if it:
        buys = [x for x in it if x["net_amt_100m"] > 0][:3]
        sells = [x for x in it if x["net_amt_100m"] < 0][:2]
        if buys:
            head = " · ".join(f"<b>{e(x['name'])}</b>(+{x['net_amt_100m']}억)" for x in buys)
            lines.append(f"👤 내부자 매수 우세: {head}")
        if sells:
            head = " · ".join(f"{e(x['name'])}({x['net_amt_100m']}억)" for x in sells)
            lines.append(f"👤 내부자 매도 우세: {head}")
    else:
        iw = data.get("insider_watch") or []
        if iw:
            head = " · ".join(f"{e(x['company'])}({x['count']}건)" for x in iw[:4])
            lines.append(f"👤 내부자·대주주 신고 몰림: {head} · 매수/매도 방향은 공시 원문 확인")

    if pos or neg:
        lines.append("")

    sr = data.get("scan_review")
    if sr and sr.get("items"):
        avg = sr["avg_ret_pct"]
        lines.append(f"🔔 어제 종가스캔 성적: 평균 {'+' if avg>0 else ''}{avg}% "
                     f"({len(sr['items'])}종목)")
        lines.append("")

    hot = [s for s in (data.get("all_stocks") or [])
           if (s.get("trend_ratio") or 0) >= 3 or (s.get("news_24h") or 0) >= 10]
    if hot:
        hot.sort(key=lambda s: -(s.get("trend_ratio") or 0))
        names = ", ".join(e(s["name"]) for s in hot[:5])
        lines.append(f"🔥 관심 급증: {names}")
        lines.append("")

    codes = parse_watchlist(
        WATCHLIST_PATH.read_text(encoding="utf-8") if WATCHLIST_PATH.exists() else "")
    events = watchlist_events(data, codes)
    if events:
        lines.append("<b>🚨 관심종목 이벤트</b>")
        lines.extend(e(x) for x in events)
        lines.append("")
    wl = watchlist_lines(data, codes)
    if wl:
        lines.append("<b>⭐ 내 관심종목</b>")
        lines.extend(e(x) for x in wl)
        lines.append("")

    lines.append(f'📈 <a href="{SITE_URL}">대시보드 전체 보기</a>')
    lines.append("<i>투자 참고 자료이며 매수·매도 추천이 아닙니다.</i>")
    return "\n".join(lines)


def watchlist_events(data, codes):
    """관심종목에 생긴 주목 이벤트: 5선 진입 / 급등락 / 공시 발생."""
    if not codes:
        return []
    out = []
    pool = {s["code"]: s for s in (data.get("all_stocks") or [])}
    names = {c: pool[c]["name"] for c in codes if c in pool}
    idea_codes = {s["code"]: s for s in (data.get("ideas") or [])}
    for c in codes:
        nm = names.get(c)
        if not nm:
            continue
        if c in idea_codes:
            days = idea_codes[c].get("idea_days")
            tag = "오늘의 5선 진입!" if days == 1 else f"5선 {days}일째 선정"
            out.append(f"· {nm} · {tag}")
        chg = pool[c].get("change_pct")
        if chg is not None and abs(chg) >= 5:
            out.append(f"· {nm} · {'급등' if chg > 0 else '급락'} "
                       f"{'+' if chg > 0 else ''}{chg:.1f}%")
    watch_names = set(names.values())
    for d in (data.get("disclosures") or []):
        if d.get("company") in watch_names:
            mark = {"positive": "호재성", "negative": "악재성"}.get(d.get("sentiment"), "")
            out.append(f"· {d['company']} · {mark} 공시: {d.get('tag')}")
    return out[:6]




def compass_lines(data, brief=False):
    """뉴스 나침반 블록 (아침·저녁 공용). brief=True면 압축판."""
    e = lambda s: html.escape(str(s or ""))
    nc = data.get("news_compass")
    if not nc:
        return []
    lines = []
    hot = nc.get("hot_themes") or []
    debuts = nc.get("debuts") or []
    if not hot and not debuts:
        return []
    lines.append("🧭 <b>뉴스 나침반</b>")
    for t in hot[:3]:
        lines.append(f"🔥 {e(t['name'])} 점화 · 기사 {t['count']}건 (평소 {t['mult']}배)")
        for s in (t.get("stocks") or [])[:3]:
            chg = s.get("change_pct")
            chg_s = "" if chg is None else f" {'+' if chg > 0 else ''}{chg:.1f}%"
            lines.append(f"   {e(s['name'])}{chg_s} · {e(s['verdict'])}")
    if debuts and not brief:
        names = " · ".join(
            f"{e(d['name'])}({d['news_24h']}건{'·호재' if (d.get('news_pos') or 0) > (d.get('news_neg') or 0) else ''})"
            for d in debuts[:4])
        lines.append(f"🐣 뉴스 데뷔: {names}")
    elif debuts:
        lines.append(f"🐣 뉴스 데뷔 {len(debuts)}종목 (대시보드 확인)")
    lines.append("")
    return lines

SCREEN_LABELS = {"vacancy": "🏦 빈집털이", "pullback": "🎯 대장주 눌림목",
                 "hotmoney": "🔥 종합 수급", "stealth": "🤫 몰래 매집",
                 "gate52": "🚪 신고가 문앞"}

def screen_lines(data):
    """조건 검색 적중 블록 (저녁 요약용) - 적중 없으면 빈 리스트."""
    e = lambda t: html.escape(str(t or ""))
    screens = data.get("screens") or {}
    out = []
    for key, label in SCREEN_LABELS.items():
        hits = screens.get(key) or []
        if hits:
            names = " · ".join(f"<b>{e(h['name'])}</b>({e(h['why'])})" for h in hits[:3])
            out.append(f"{label}: {names}")
    if out:
        out.insert(0, "🔎 <b>조건 검색 적중</b>")
        out.append("")
    return out


def swing_lines(data, top=3):
    """5일선 스윙 후보 상위 (저녁 요약용) - 후보 없으면 빈 리스트 (0픽셀)."""
    e = lambda t: html.escape(str(t or ""))
    swing = data.get("swing") or []
    if not swing:
        return []
    out = [f'📈 <b><a href="{SITE_URL}swing.html">스윙 후보</a></b> (5일선 단기)']
    for p in swing[:top]:
        setup = e(p["setups"][0]) if p.get("setups") else ""
        tail = f" · {setup}" if setup else ""
        out.append(f"<b>{e(p.get('name'))}</b> {p.get('swing')}점{tail} · "
                   f"목표 +{p.get('target_pct')}% / 손절 {p.get('stop_pct')}%")
    out.append("")
    return out


def swing_exit_signal(s):
    """종목 1개의 청산 신호 판정 (이평선 기준). 반환: (레벨, 문구) 또는 None.
    🔴 생명선(20일선) 이탈 = 손절 검토 · 🟡 5일선 이탈 = 단기 주의."""
    px, ma5, ma20 = s.get("price"), s.get("ma5"), s.get("ma20")
    if not (px and ma20):
        return None
    if px < ma20:
        return ("stop", f"20일선(생명선) {fmt_num(ma20, 0)} 이탈, 손절 검토")
    if ma5 and px < ma5:
        return ("warn", f"5일선 {fmt_num(ma5, 0)} 이탈, 단기 주의")
    return None


def swing_exit_lines(data):
    """관심종목(watchlist.txt)의 청산 신호 (저녁 요약용) - 신호 있을 때만 (0픽셀)."""
    e = lambda t: html.escape(str(t or ""))
    try:
        codes = parse_watchlist(WATCHLIST_PATH.read_text(encoding="utf-8")
                                if WATCHLIST_PATH.exists() else "")
    except Exception:
        codes = []
    if not codes:
        return []
    by = {s.get("code"): s for s in (data.get("all_stocks") or [])}
    hits = []
    for c in codes:
        s = by.get(c)
        if not s:
            continue
        sig = swing_exit_signal(s)
        if sig:
            hits.append((sig[0], s.get("name"), sig[1]))
    if not hits:
        return []
    order = {"stop": 0, "warn": 1}
    hits.sort(key=lambda h: order.get(h[0], 9))
    out = ["🚪 <b>내 종목 청산 신호</b>"]
    for lvl, name, why in hits[:5]:
        icon = "🔴" if lvl == "stop" else "🟡"
        out.append(f"{icon} <b>{e(name)}</b> · {e(why)}")
    out.append("")
    return out


def stale_notice(data, today=None):
    """침묵 정지 방어: 평일인데 데이터 기준일이 오늘이 아니면 경고 라인."""
    today = today or kst_today()
    md = data.get("market_date") or ""
    try:
        t = datetime.strptime(today, "%Y-%m-%d")
    except Exception:
        return []
    if t.weekday() >= 5 or not md or md == today:
        return []
    return [f"⚠️ 오늘({today}) 새 데이터가 수집되지 않았습니다 · 기준일 {md}",
            "휴장일이면 정상 · 평일인데 이틀 연속이면 PLAYBOOK 점검 필요", ""]


def mine_lines(data):
    """지뢰 경고 (저녁 요약용) - 감지된 날만, 관심종목 겹침은 강조."""
    e = lambda t: html.escape(str(t or ""))
    mines = data.get("mines") or []
    if not mines:
        return []
    top = mines[0]
    extra = f" 외 {len(mines)-1}종목" if len(mines) > 1 else ""
    out = [f"💣 위험 신호 누적: <b>{e(top['name'])}</b>({top['score']}점 · "
           f"{e(top['reasons'][0] if top.get('reasons') else '')}){extra}"]
    try:
        codes = parse_watchlist(WATCHLIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        codes = []
    hit = [m for m in mines if m.get("code") in codes]
    if hit:
        out.append("⚠️ <b>관심종목 중 지뢰 감지</b>: " +
                   " · ".join(f"{e(m['name'])}({m['score']}점)" for m in hit[:3]))
    out.append("")
    return out


def build_evening_message(data):
    """저녁 마감 요약 - 짧은 버전."""
    e = lambda s: html.escape(str(s or ""))
    md = (data.get("market_date") or "").replace("-", ".")
    idx = data.get("indices") or {}
    k, q = idx.get("KOSPI") or {}, idx.get("KOSDAQ") or {}
    lines = [f"🌙 <b>마감 요약</b>  <i>({e(md)})</i>", ""]
    lines.extend(stale_notice(data))
    lines.extend(swing_exit_lines(data))
    lines.extend(swing_lines(data))
    lines.extend(screen_lines(data))
    lines.extend(mine_lines(data))
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
    lines.extend([""] if compass_lines(data, brief=True) else [])
    lines.extend(compass_lines(data, brief=True))
    movers = sorted([s for s in (data.get("all_stocks") or [])
                     if abs(s.get("change_pct") or 0) >= 8],
                    key=lambda s: -abs(s.get("change_pct") or 0))[:3]
    if movers:
        lines.append("🚀 오늘 급등락: " + " · ".join(
            f"{e(s['name'])} {'+' if s['change_pct']>0 else ''}{s['change_pct']:.1f}%"
            for s in movers))
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

    race = (data.get("strategy_race") or {}).get("rank") or []
    if race:
        lines.append("")
        lines.append("<b>🏁 전략 리그 순위</b>")
        for i, x in enumerate(race, 1):
            v = x["total_pct"]
            lines.append(f"{i}위 {e(x['name'])} {'+' if v > 0 else ''}{v}%")
    lines.append("")
    lines.extend(weekly_extra_lines(data))
    lines.append(f'📈 <a href="{SITE_URL}">대시보드에서 상세 보기</a>')
    lines.append("<i>투자 참고 자료이며 매수·매도 추천이 아닙니다.</i>")
    return "\n".join(lines)


def weekly_extra_lines(data, today=None):
    """주간 결산 끝: 졸업생 복기(S12) + 튜닝 제안(S13) + 시스템 건강 체크
    + (매월 첫 일요일) 백업 리마인더."""
    today = today or kst_today()
    out = []
    # S12: 5선 졸업생 복기 · 내보낸 판단이 옳았나
    grads = data.get("graduates") or []
    if grads:
        best, worst = grads[0], grads[-1]
        parts = []
        if best.get("ret_pct", 0) >= 3:
            parts.append(f"아쉬움 <b>{html.escape(best['name'])}</b> "
                         f"제외 후 +{best['ret_pct']}%")
        if worst.get("ret_pct", 0) <= -3 and worst is not best:
            parts.append(f"잘 내보냄 <b>{html.escape(worst['name'])}</b> "
                         f"{worst['ret_pct']}%")
        if parts:
            out.append("🎓 졸업생 복기: " + " · ".join(parts))
    # S13: 전략 리그 기반 튜닝 제안
    rank = (data.get("strategy_race") or {}).get("rank") or []
    if len(rank) >= 2:
        leader = rank[0]
        base = next((x for x in rank if "기본" in x.get("name", "")), None)
        if base and leader is not base:
            margin = round(leader["total_pct"] - base["total_pct"], 1)
            if margin >= 2.0:
                out.append(f"💡 튜닝 제안: <b>{html.escape(leader['name'])}</b>이 "
                           f"기본형을 {margin}%p 앞서는 중 · 2~3주 지속되면 "
                           f"CONFIG 가중치 반영 검토 (README 튜닝 가이드)")
    # 시스템 건강: 최근 7일간 수집된 거래일 수
    trend = data.get("kospi_trend") or []
    try:
        from datetime import date
        t = date(*map(int, today.split("-")))
        recent = [p for p in trend if p.get("d")
                  and 0 <= (t - date(*map(int, p["d"].split("-")))).days <= 6]
        n = len({p["d"] for p in recent})
    except Exception:
        n = 0
    if trend:
        latest = trend[-1].get("d", "?")
        out.append(f"🔧 시스템: 이번 주 수집 {n}일 · 최신 데이터 {latest}")
    # 매월 첫 일요일 = 백업의 날
    if int(today.split("-")[2]) <= 7:
        out.append("💾 <b>백업의 날</b>: ① 저장소에서 Code→Download ZIP "
                   "② 대시보드 포트폴리오 백업(CSV) · 자세한 방법은 PLAYBOOK.md 7번")
    if out:
        out.append("")
    return out


def clamp_telegram(text, limit=4096):
    """텔레그램 4096자 제한 방어. 초과 시 줄 경계에서 잘라 태그 균형 유지 + 안내 한 줄.
    (줄 단위로 자르므로 <b>…</b> 같은 한 줄 안의 태그가 중간에 끊기지 않는다.)"""
    if len(text) <= limit:
        return text
    notice = "\n…(길어서 일부 생략 · 대시보드에서 전체 확인)"
    budget = limit - len(notice)
    cut = text.rfind("\n", 0, budget)
    if cut < budget // 2:            # 적당한 줄 경계가 없으면 통째로 자름
        cut = budget
    return text[:cut] + notice


def send(token, chat_id, text, retries=3, wait_s=4):
    """텔레그램 발송. 일시적 오류(네트워크·서버)는 재시도, 설정 오류(400번대)는 즉시 중단."""
    text = clamp_telegram(text)
    last = ""
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=20)
            if r.status_code == 200 and r.json().get("ok"):
                return True
            last = f"{r.status_code} {r.text[:300]}"
            if 400 <= r.status_code < 500 and r.status_code != 429:
                break  # 토큰/챗ID 오류 등은 재시도해도 소용없음
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            print(f"발송 실패({attempt}/{retries}) - {wait_s}초 후 재시도: {last}")
            time.sleep(wait_s)
    print(f"텔레그램 발송 실패: {last}")
    return False


SENT_LOG = Path(__file__).parent / "sent_log.json"

def mark_sent(mode):
    """발송 장부 기록 (예비 알람의 중복 발송 방지용)."""
    try:
        log = json.loads(SENT_LOG.read_text(encoding="utf-8")) if SENT_LOG.exists() else {}
    except Exception:
        log = {}
    log[mode] = kst_today()
    try:
        SENT_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        print(f"발송 장부 기록 실패(무시): {exc}")

def already_sent(mode):
    try:
        log = json.loads(SENT_LOG.read_text(encoding="utf-8"))
        return log.get(mode) == kst_today()
    except Exception:
        return False

def kst_today():
    return (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="발송 없이 내용만 출력")
    ap.add_argument("--evening", action="store_true", help="저녁 마감 요약 (짧은 버전)")
    ap.add_argument("--weekly", action="store_true", help="일요일 주간 결산")
    ap.add_argument("--if-not-sent", action="store_true",
                    help="오늘 이미 발송했으면 조용히 종료 (예비 알람용)")
    ap.add_argument("--data", default=str(DATA_PATH))
    args = ap.parse_args()

    mode = "weekly" if args.weekly else "evening" if args.evening else "morning"
    if args.if_not_sent and already_sent(mode):
        print(f"오늘 {mode} 발송 완료 기록 있음 - 예비 알람 조용히 종료")
        return

    try:
        data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"data.json 읽기 실패: {exc}")
        sys.exit(1)

    if data.get("sample"):
        print("샘플 데이터 상태 - 발송 생략 (첫 수집 후 발송됩니다)")
        return

    # 휴장일 달력: 저녁 요약은 쉬고, 아침 브리핑엔 안내 한 줄 (모듈 없으면 기존 동작)
    holiday = None
    try:
        import holidays_kr
        holiday = holidays_kr.closed_reason()
    except ImportError:
        pass
    if holiday and holiday != "주말" and mode == "evening":
        print(f"휴장일({holiday}): 저녁 요약 발송 생략")
        return

    msg = (build_weekly_message(data) if args.weekly
           else build_evening_message(data) if args.evening
           else build_message(data))
    if holiday and holiday != "주말" and mode == "morning":
        msg = f"📅 오늘은 휴장일입니다 ({holiday}). 국내장 알림은 다음 거래일에 재개됩니다.\n\n" + msg

    if args.dry_run:
        print(msg)
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 시크릿이 없어 발송 생략")
        return

    if send(token, chat_id, msg):
        mark_sent(mode)
        print("브리핑 발송 완료")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
