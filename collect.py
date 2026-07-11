#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
국내장 투자 아이디어 대시보드 - 데이터 수집 파이프라인

데이터 소스 (모두 무료, 로그인 불필요):
  1. 네이버 증권 모바일 JSON API  - 시세/시가총액/PER/PBR/배당 (m.stock.naver.com)
  2. 네이버 증권 투자자별 매매동향 - 외국인/기관 순매매 (finance.naver.com/item/frgn.naver)
  3. DART RSS                     - 당일 공시 (dart.fss.or.kr/api/todayRSS.xml)
  4. OpenDART API (선택)          - DART_API_KEY 환경변수 설정 시 최근 3일 공시로 확장

실행:
  python collect.py                 # 전체 수집 → docs/data.json
  python collect.py --sample        # 오프라인 샘플 데이터 생성 (테스트용)
  python collect.py --max-universe 30   # 소규모 테스트 실행

주의: GitHub Actions 등 자유로운 네트워크 환경에서 실행해야 합니다.
"""

import argparse
import json
import os
import random
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
OUT_PATH = ROOT / "data.json"

# ===========================================================================
# 튜닝 파라미터 - 점수 산식과 필터를 조절하려면 여기만 수정하면 됩니다.
# (자세한 설명은 README.md의 '튜닝 가이드' 참고)
# ===========================================================================
CONFIG = {
    # 유니버스
    "kospi_n": 200,             # 코스피 시총 상위 몇 종목
    "kosdaq_n": 100,            # 코스닥 시총 상위 몇 종목
    # 아이디어 선정
    "idea_count": 5,            # 오늘의 아이디어 개수
    "idea_min_mktcap": 3000,    # 최소 시가총액 (억원)
    # 수급 점수 (최대 40)
    "flow_f_streak_cap": 10,    # 외국인 연속일 상한 (x2점 = 최대 20)
    "flow_i_streak_cap": 5,     # 기관 연속일 상한 (x2점 = 최대 10)
    "flow_both_bonus": 5,       # 외인+기관 동반(각 3일↑) 보너스
    "flow_big_amt_100m": 100,   # 외인 5일 순매수 이 금액(억)↑이면 +5
    # 밸류 점수 (최대 40)
    "value_pbr_pts": 15,        # 저PBR 백분위 배점
    "value_dvr_pts": 15,        # 고배당 백분위 배점
    "value_per_max": 12,        # PER 이 값 미만이면 +10
    # 모멘텀 점수 (최대 10): 52주 최고가 대비 현재가 비율 구간
    "mom_tiers": [(0.95, 10.0), (0.90, 7.0), (0.85, 4.0)],
    # 품질 가드
    "guard_min_universe": 150,
    "guard_min_flow_ratio": 0.5,
    "guard_min_fund_ratio": 0.4,
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
           "Referer": "https://m.stock.naver.com/"}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

REQUEST_DELAY = 0.12          # 서버 예의용 딜레이(초)
TIMEOUT = 15

# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------

def to_num(v, default=None):
    """'74,300' / '2.15%' / '12.34배' / 1234 → float. 실패 시 default."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("%", "").replace("배", "")
    s = s.replace("원", "").replace("+", "").replace("배", "").strip()
    if s in ("", "-", "N/A", "None", "null"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def get_json(url, retries=3, delay=1.0):
    for i in range(retries):
        try:
            r = SESSION.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
        except Exception:
            if i == retries - 1:
                raise
        time.sleep(delay * (i + 1))
    raise RuntimeError(f"failed: {url}")


def get_text(url, retries=3, delay=1.0, encoding=None):
    for i in range(retries):
        try:
            r = SESSION.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                if encoding:
                    r.encoding = encoding
                elif r.apparent_encoding and "euc" in (r.apparent_encoding or "").lower():
                    r.encoding = r.apparent_encoding
                return r.text
        except Exception:
            if i == retries - 1:
                raise
        time.sleep(delay * (i + 1))
    raise RuntimeError(f"failed: {url}")


# ---------------------------------------------------------------------------
# 1) 유니버스: 시가총액 상위 종목 (KOSPI + KOSDAQ)
# ---------------------------------------------------------------------------

def fetch_universe(kospi_n=None, kosdaq_n=None):
    """네이버 모바일 API에서 시총 상위 종목 목록. 실패 시 데스크톱 페이지 폴백."""
    kospi_n = kospi_n or CONFIG["kospi_n"]
    kosdaq_n = kosdaq_n or CONFIG["kosdaq_n"]
    stocks = []
    for market, want in (("KOSPI", kospi_n), ("KOSDAQ", kosdaq_n)):
        got = 0
        for page in range(1, (want // 100) + 2):
            if got >= want:
                break
            url = (f"https://m.stock.naver.com/api/stocks/marketValue/"
                   f"{market}?page={page}&pageSize=100")
            try:
                data = get_json(url)
            except Exception:
                break
            items = data.get("stocks") or data.get("result", {}).get("stocks") or []
            if not items:
                break
            for it in items:
                s = parse_market_value_item(it, market)
                if s:
                    stocks.append(s)
                    got += 1
                    if got >= want:
                        break
            time.sleep(REQUEST_DELAY)
        if got == 0:
            # 폴백: 데스크톱 시가총액 페이지 크롤링
            stocks.extend(fetch_universe_fallback(market, want))
    return stocks


def parse_market_value_item(it, market):
    """marketValue API의 종목 항목 → 표준 dict."""
    code = it.get("itemCode") or it.get("cd") or it.get("code")
    name = it.get("stockName") or it.get("nm") or it.get("name")
    if not code or not name:
        return None
    price = to_num(it.get("closePrice") or it.get("nv"))
    rate = to_num(it.get("fluctuationsRatio") or it.get("cr"))
    # compareDirection: 하락이면 등락률 부호 보정 (API가 부호 없이 줄 때 대비)
    crt = str(it.get("compareToPreviousPrice", {}).get("code", "")
              if isinstance(it.get("compareToPreviousPrice"), dict)
              else it.get("compareToPreviousPrice", ""))
    if rate is not None and rate > 0 and crt in ("5", "4"):   # 5=하락, 4=하한
        rate = -rate
    mv = to_num(it.get("marketValue") or it.get("marketSum"))  # 단위: 억원
    vol = to_num(it.get("accumulatedTradingVolume") or it.get("aq"))
    return {"code": str(code).zfill(6), "name": name, "market": market,
            "price": price, "change_pct": rate, "mktcap_100m": mv, "volume": vol}


def fetch_universe_fallback(market, want):
    """데스크톱 sise_market_sum 페이지 크롤링 폴백 (pandas 필요)."""
    try:
        import pandas as pd
    except ImportError:
        return []
    from io import StringIO
    sosok = 0 if market == "KOSPI" else 1
    out = []
    for page in range(1, (want // 50) + 2):
        if len(out) >= want:
            break
        url = (f"https://finance.naver.com/sise/sise_market_sum.naver"
               f"?sosok={sosok}&page={page}")
        try:
            html = get_text(url, encoding="euc-kr")
            tables = pd.read_html(StringIO(html))
        except Exception:
            break
        best = max(tables, key=lambda t: t.shape[0] * t.shape[1])
        best = best.dropna(subset=["종목명"]) if "종목명" in best.columns else best.dropna(how="all")
        codes = dict(re.findall(r'href="/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>', html))
        name2code = {v: k for k, v in codes.items()}
        for _, row in best.iterrows():
            name = str(row.get("종목명", "")).strip()
            if not name or name == "nan" or name not in name2code:
                continue
            out.append({"code": name2code[name], "name": name, "market": market,
                        "price": to_num(row.get("현재가")),
                        "change_pct": to_num(str(row.get("등락률", "")).replace("%", "")),
                        "mktcap_100m": to_num(row.get("시가총액")),
                        "volume": to_num(row.get("거래량"))})
            if len(out) >= want:
                break
        time.sleep(REQUEST_DELAY)
    return out


# ---------------------------------------------------------------------------
# 2) 펀더멘털: PER / PBR / 배당수익률 (종목별 integration API)
# ---------------------------------------------------------------------------

def fetch_fundamentals(code):
    """m.stock.naver.com/api/stock/{code}/integration → dict(per, pbr, dvr, eps, bps)"""
    url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    data = get_json(url)
    return parse_integration(data)


def parse_integration(data):
    infos = data.get("totalInfos") or []
    m = {}
    for info in infos:
        c = (info.get("code") or "").lower()
        key = (info.get("key") or "")
        val = info.get("value")
        if c:
            m[c] = val
        # 키 이름 기반 폴백
        if "PER" in key and "per" not in m:
            m["per"] = val
        if "PBR" in key and "pbr" not in m:
            m["pbr"] = val
        if ("배당수익" in key or "배당" in key) and "dvr" not in m:
            m["dvr"] = val
        if "EPS" in key and "eps" not in m:
            m["eps"] = val
        if "BPS" in key and "bps" not in m:
            m["bps"] = val
        if "52주 최고" in key or "52주최고" in key:
            m["h52"] = val
    dvr = m.get("dvr") or m.get("dividend") or m.get("dividendrate")
    h52 = m.get("h52") or m.get("highpriceof52weeks")
    return {"per": to_num(m.get("per")), "pbr": to_num(m.get("pbr")),
            "dvr": to_num(dvr), "eps": to_num(m.get("eps")),
            "bps": to_num(m.get("bps")), "h52": to_num(h52)}


# ---------------------------------------------------------------------------
# 3) 수급: 외국인/기관 순매매 (frgn 페이지 크롤링)
# ---------------------------------------------------------------------------

FRGN_ROW_RE = re.compile(
    r"(\d{4}\.\d{2}\.\d{2})"      # 날짜
)

def fetch_investor_flows(code):
    """finance.naver.com/item/frgn.naver → 최근 거래일별 기관/외국인 순매매량 리스트
    반환: [{date, close, inst, frgn}] (최신순)"""
    url = f"https://finance.naver.com/item/frgn.naver?code={code}"
    html = get_text(url, encoding="euc-kr")
    return parse_frgn_html(html)


def parse_frgn_html(html):
    """frgn 페이지의 일별 매매 테이블 파싱 (pandas 미의존, 정규식/문자열 기반)."""
    rows = []
    # 테이블 행 단위로 자르기: 날짜가 포함된 <tr> 블록
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        if "20" not in tr:
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 7:
            continue
        cells = [re.sub(r"<[^>]+>", "", td).strip() for td in tds]
        if not re.match(r"\d{4}\.\d{2}\.\d{2}$", cells[0]):
            continue
        date = cells[0]
        close = to_num(cells[1])
        # 열 구성: 날짜/종가/전일비/등락률/거래량/기관순매매/외국인순매매/보유주수/보유율
        inst = to_num(cells[5])
        frgn = to_num(cells[6])
        if close is None or (inst is None and frgn is None):
            continue
        rows.append({"date": date, "close": close,
                     "inst": inst or 0.0, "frgn": frgn or 0.0})
    return rows  # 페이지 특성상 최신순


def flow_metrics(rows, price=None):
    """일별 순매매 리스트(최신순) → 수급 지표 계산."""
    if not rows:
        return None
    px = price or rows[0]["close"]

    def streak(key):
        n = 0
        for r in rows:
            if r[key] > 0:
                n += 1
            else:
                break
        return n

    def cum(key, days):
        return sum(r[key] for r in rows[:days])

    f5, f20 = cum("frgn", 5), cum("frgn", 20)
    i5, i20 = cum("inst", 5), cum("inst", 20)
    return {
        "f_streak": streak("frgn"), "i_streak": streak("inst"),
        "f_5d": f5, "f_20d": f20, "i_5d": i5, "i_20d": i20,
        "f_5d_amt_100m": round(f5 * px / 1e8, 1),    # 억원 환산
        "i_5d_amt_100m": round(i5 * px / 1e8, 1),
        "f_20d_amt_100m": round(f20 * px / 1e8, 1),
        "i_20d_amt_100m": round(i20 * px / 1e8, 1),
        "days": len(rows),
    }


# ---------------------------------------------------------------------------
# 3.5) 업종 매핑 (WICS 대분류, wiseindex.com)
# ---------------------------------------------------------------------------

WICS_SECTORS = {
    "G10": "에너지", "G15": "소재", "G20": "산업재", "G25": "경기소비재",
    "G30": "필수소비재", "G35": "건강관리", "G40": "금융", "G45": "IT",
    "G50": "커뮤니케이션", "G55": "유틸리티",
}


def fetch_sector_map(market_date=None, max_back_days=7):
    """WICS 업종 구성종목 → {종목코드: 업종명}. 실패해도 빈 dict (선택 기능)."""
    base_day = None
    if market_date:
        try:
            base_day = datetime.strptime(market_date, "%Y-%m-%d")
        except ValueError:
            pass
    if base_day is None:
        base_day = datetime.now(KST).replace(tzinfo=None)
    out = {}
    for back in range(max_back_days):
        dt = (base_day - timedelta(days=back)).strftime("%Y%m%d")
        got_any = False
        for sec_cd, sec_nm in WICS_SECTORS.items():
            url = (f"https://www.wiseindex.com/Index/GetIndexComponets"
                   f"?ceil_yn=0&dt={dt}&sec_cd={sec_cd}")
            try:
                data = get_json(url, retries=1)
            except Exception:
                continue
            for it in parse_sector_items(data, sec_nm):
                out[it[0]] = it[1]
                got_any = True
            time.sleep(REQUEST_DELAY)
        if got_any:
            break   # 해당 날짜에 데이터가 있으면 종료 (휴장일이면 하루씩 과거로)
        out = {}
    return out


def parse_sector_items(data, sec_nm_fallback):
    """wiseindex 응답 → [(종목코드, 업종명)]"""
    out = []
    for it in (data or {}).get("list") or []:
        code = str(it.get("CMP_CD") or "").zfill(6)
        if not code.strip("0"):
            continue
        name = it.get("SEC_NM_KOR") or sec_nm_fallback
        out.append((code, name))
    return out


# ---------------------------------------------------------------------------
# 4) DART 공시 시그널
# ---------------------------------------------------------------------------

POSITIVE_KW = [
    ("자기주식취득", "자사주 매입", 10),
    ("자기주식 취득", "자사주 매입", 10),
    ("주식소각", "자사주 소각", 15),
    ("소각결정", "자사주 소각", 15),
    ("무상증자", "무상증자", 10),
    ("주식분할", "액면분할", 5),
    ("단일판매ㆍ공급계약체결", "공급계약", 8),
    ("공급계약체결", "공급계약", 8),
    ("현금ㆍ현물배당결정", "배당결정", 5),
    ("자기주식취득신탁계약체결", "자사주 신탁", 8),
]
NEGATIVE_KW = [
    ("유상증자결정", "유상증자", -10),
    ("전환사채권발행결정", "CB 발행", -8),
    ("신주인수권부사채권발행결정", "BW 발행", -8),
    ("감자결정", "감자", -10),
    ("관리종목", "관리종목", -15),
    ("상장폐지", "상폐 위험", -20),
    ("자기주식처분결정", "자사주 처분", -5),
    ("소송등의제기", "소송 제기", -8),
    ("횡령ㆍ배임", "횡령·배임", -20),
    ("영업정지", "영업정지", -15),
    ("전환가액의조정", "전환가 조정", -3),
]
WATCH_KW = [
    ("임원ㆍ주요주주특정증권등소유상황보고서", "내부자 지분변동", 0),
    ("최대주주변경", "최대주주 변경", 0),
    ("주식등의대량보유상황보고서", "5%룰 보고", 0),
    ("회사합병결정", "합병", 0),
    ("영업양수결정", "영업양수", 0),
    ("타법인주식및출자증권취득결정", "타법인 취득", 0),
    ("유형자산취득결정", "자산 취득", 0),
]


def classify_disclosure(title):
    """공시 제목 → (태그, 감성, 점수) 또는 None(관심 없음)."""
    for kw, tag, score in POSITIVE_KW:
        if kw in title:
            return tag, "positive", score
    for kw, tag, score in NEGATIVE_KW:
        if kw in title:
            return tag, "negative", score
    for kw, tag, score in WATCH_KW:
        if kw in title:
            return tag, "watch", score
    return None


def fetch_dart_rss():
    """DART 당일 공시 RSS → [{time, company, title, url, market}]"""
    url = "https://dart.fss.or.kr/api/todayRSS.xml"
    xml_text = get_text(url)
    return parse_dart_rss(xml_text)


def parse_dart_rss(xml_text):
    out = []
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        # 선언부 인코딩 불일치 등 → 관대한 재시도
        xml_text = re.sub(r"encoding=\"[^\"]+\"", 'encoding="utf-8"', xml_text)
        root = ET.fromstring(xml_text.encode("utf-8"))
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        # 제목 형식: "(유가)회사명 - 보고서명" 또는 "(코스닥)..."
        m = re.match(r"\((유가|코스닥|코넥스|기타)\)\s*(.+?)\s*-\s*(.+)", title)
        if not m:
            company, report, market = title, title, ""
        else:
            market = {"유가": "KOSPI", "코스닥": "KOSDAQ"}.get(m.group(1), m.group(1))
            company, report = m.group(2).strip(), m.group(3).strip()
        out.append({"time": pub, "company": company, "title": report,
                    "url": link, "market": market})
    return out


def fetch_dart_openapi(api_key, days=3):
    """OpenDART list.json — 키가 있으면 최근 N일 공시로 확장."""
    end = datetime.now(KST)
    bgn = end - timedelta(days=days)
    out, page = [], 1
    while page <= 10:
        url = ("https://opendart.fss.or.kr/api/list.json"
               f"?crtfc_key={api_key}&bgn_de={bgn:%Y%m%d}&end_de={end:%Y%m%d}"
               f"&page_no={page}&page_count=100")
        data = get_json(url)
        if data.get("status") != "000":
            break
        for it in data.get("list", []):
            out.append({
                "time": it.get("rcept_dt", ""),
                "company": it.get("corp_name", ""),
                "title": it.get("report_nm", ""),
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it.get('rcept_no','')}",
                "market": {"Y": "KOSPI", "K": "KOSDAQ"}.get(it.get("corp_cls"), ""),
            })
        if page >= int(data.get("total_page", 1)):
            break
        page += 1
        time.sleep(REQUEST_DELAY)
    return out


def build_disclosure_signals(raw_items):
    """공시 원본 → 시그널만 필터링 + 태그."""
    signals = []
    for it in raw_items:
        cls = classify_disclosure(it["title"])
        if not cls:
            continue
        tag, senti, score = cls
        signals.append({**it, "tag": tag, "sentiment": senti, "score": score})
    return signals


# ---------------------------------------------------------------------------
# 5) 지수 스냅샷
# ---------------------------------------------------------------------------

def fetch_indices():
    """지수 스냅샷. 반환: (지수dict, 최근거래일 'YYYY-MM-DD' 또는 None)"""
    out, traded = {}, None
    for idx in ("KOSPI", "KOSDAQ"):
        try:
            data = get_json(
                f"https://polling.finance.naver.com/api/realtime/domestic/index/{idx}")
            d = (data.get("datas") or [{}])[0]
            val = to_num(d.get("closePrice"))
            rate = to_num(d.get("fluctuationsRatio"))
            crt = str(d.get("compareToPreviousPrice", {}).get("code", "")
                      if isinstance(d.get("compareToPreviousPrice"), dict) else "")
            if rate and rate > 0 and crt in ("5", "4"):
                rate = -rate
            out[idx] = {"value": val, "change_pct": rate}
            lta = str(d.get("localTradedAt") or "")   # 예: 2026-07-10T18:01:33+09:00
            m = re.match(r"(\d{4}-\d{2}-\d{2})", lta)
            if m and not traded:
                traded = m.group(1)
        except Exception:
            out[idx] = {"value": None, "change_pct": None}
        time.sleep(REQUEST_DELAY)
    return out, traded


# ---------------------------------------------------------------------------
# 품질 가드 & 휴장일 판정
# ---------------------------------------------------------------------------

def validate_collection(stocks, min_universe=None, min_flow=None, min_fund=None):
    """수집 결과 품질 검증. 치명적 문제 리스트 반환 (비어 있으면 통과).

    소스 장애로 데이터가 반쪽짜리일 때 엉터리 대시보드가 배포되는 것을 막는다.
    """
    min_universe = min_universe or CONFIG["guard_min_universe"]
    min_flow = min_flow or CONFIG["guard_min_flow_ratio"]
    min_fund = min_fund or CONFIG["guard_min_fund_ratio"]
    fatal = []
    n = len(stocks)
    if n < min_universe:
        fatal.append(f"유니버스 {n}종목 (기준 {min_universe} 미만) - 소스 장애 의심")
        return fatal
    priced = sum(1 for s in stocks if s.get("price"))
    if priced < n * 0.7:
        fatal.append(f"가격 수집 {priced}/{n} (70% 미만)")
    flows = sum(1 for s in stocks if s.get("days"))
    if flows < n * min_flow:
        fatal.append(f"수급 수집 {flows}/{n} ({int(min_flow*100)}% 미만)")
    funds = sum(1 for s in stocks if s.get("pbr") is not None or s.get("per") is not None)
    if funds < n * min_fund:
        fatal.append(f"펀더멘털 수집 {funds}/{n} ({int(min_fund*100)}% 미만)")
    return fatal


def load_previous(path):
    """기존 data.json 로드 (없거나 샘플이면 None)."""
    try:
        prev = json.loads(Path(path).read_text(encoding="utf-8"))
        return None if prev.get("sample") else prev
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 전일 대비 변화 (히스토리 기반) - 데이터가 쌓이면 자동 활성화
# ---------------------------------------------------------------------------

def apply_flow_delta(stocks, prev):
    """전일 데이터 대비 수급점수 변화량(flow_delta)을 각 종목에 기록."""
    if not prev:
        return stocks
    pool = prev.get("all_stocks") or (prev.get("flow_scan") or []) + (prev.get("ideas") or [])
    prev_map = {p["code"]: p.get("flow_score") for p in pool if p.get("code")}
    for s in stocks:
        pf = prev_map.get(s["code"])
        if pf is not None and s.get("flow_score") is not None:
            s["flow_delta"] = round(s["flow_score"] - pf, 1)
    return stocks


def load_history_idea_codes(hist_dir, before_date, limit=15):
    """히스토리 폴더에서 before_date 이전 날짜들의 '아이디어 코드 집합' 리스트 (최신순)."""
    out = []
    try:
        files = sorted(Path(hist_dir).glob("*.json"), reverse=True)
    except Exception:
        return out
    for f in files[:limit * 2]:
        day = f.stem                      # YYYY-MM-DD
        if before_date and day >= before_date:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append(set(s["code"] for s in d.get("ideas", [])))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def build_performance(hist_dir, stocks, indices, market_date, max_days=30):
    """과거 '아이디어 5선'의 현재까지 수익률 추적 (히스토리 기반).

    각 과거 날짜의 5선을 그날 가격 → 오늘 가격으로 평가하고,
    같은 기간 KOSPI 수익률과 비교한다.
    """
    price_map = {s["code"]: s.get("price") for s in stocks if s.get("price")}
    kospi_now = ((indices or {}).get("KOSPI") or {}).get("value")
    records = []
    try:
        files = sorted(Path(hist_dir).glob("*.json"), reverse=True)[:max_days]
    except Exception:
        files = []
    for f in files:
        day = f.stem
        if market_date and day >= market_date:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        items, rets = [], []
        for s in d.get("ideas") or []:
            p0, p1 = s.get("price"), price_map.get(s.get("code"))
            if p0 and p1:
                r = round((p1 / p0 - 1) * 100, 2)
                rets.append(r)
                items.append({"code": s["code"], "name": s["name"],
                              "entry": p0, "ret_pct": r})
        if not rets:
            continue
        k0 = ((d.get("indices") or {}).get("KOSPI") or {}).get("value")
        kret = round((kospi_now / k0 - 1) * 100, 2) if (k0 and kospi_now) else None
        records.append({"date": day,
                        "avg_ret_pct": round(sum(rets) / len(rets), 2),
                        "kospi_ret_pct": kret, "ideas": items})
    if not records:
        return {"days": 0, "records": []}
    wins = sum(1 for r in records if r["avg_ret_pct"] > 0)
    vs = [r for r in records if r["kospi_ret_pct"] is not None]
    beat = sum(1 for r in vs if r["avg_ret_pct"] > r["kospi_ret_pct"])
    return {
        "days": len(records),
        "summary": {
            "avg_ret_pct": round(sum(r["avg_ret_pct"] for r in records) / len(records), 2),
            "win_rate_pct": round(wins / len(records) * 100),
            "beat_kospi_pct": round(beat / len(vs) * 100) if vs else None,
        },
        "records": records[:10],
    }


def build_index_trend(hist_dir, indices, market_date, key="KOSPI", days=20):
    """히스토리에서 지수 추이 추출 (스파크라인용). [{d, v}] 날짜 오름차순."""
    pts = []
    try:
        files = sorted(Path(hist_dir).glob("*.json"))
    except Exception:
        files = []
    for f in files:
        if market_date and f.stem >= market_date:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        v = ((d.get("indices") or {}).get(key) or {}).get("value")
        if v:
            pts.append({"d": f.stem, "v": v})
    now_v = ((indices or {}).get(key) or {}).get("value")
    if now_v and market_date:
        pts.append({"d": market_date, "v": now_v})
    return pts[-days:]


def apply_idea_streaks(ideas, past_idea_sets):
    """아이디어 종목별 연속 선정일수(idea_days) 계산. 오늘 포함 1부터 시작."""
    for s in ideas:
        days = 1
        for past in past_idea_sets:       # 최신순으로 연속 여부 확인
            if s["code"] in past:
                days += 1
            else:
                break
        s["idea_days"] = days
    return ideas


def is_holiday_rerun(prev, market_date):
    """직전 발행분과 장 기준일이 같으면 휴장일 재실행 → 갱신 생략."""
    return bool(prev and market_date
                and prev.get("market_date") == market_date)


# ---------------------------------------------------------------------------
# 6) 점수화 & 아이디어 브리핑
# ---------------------------------------------------------------------------

def percentile_rank(sorted_vals, v):
    """v가 sorted_vals에서 갖는 백분위 (0~1, 낮을수록 0)."""
    if not sorted_vals or v is None:
        return None
    import bisect
    i = bisect.bisect_left(sorted_vals, v)
    return i / max(1, len(sorted_vals) - 1) if len(sorted_vals) > 1 else 0.5


def score_stocks(stocks, disclosure_signals):
    """수급(40) + 밸류(40) + 모멘텀(10) + 공시(±20) = 종합점수."""
    pbrs = sorted(s["pbr"] for s in stocks if s.get("pbr"))
    dvrs = sorted(s["dvr"] for s in stocks if s.get("dvr") is not None)

    disc_by_company = {}
    for d in disclosure_signals:
        disc_by_company.setdefault(d["company"], []).append(d)

    for s in stocks:
        flow_pts, val_pts, disc_pts = 0.0, 0.0, 0.0
        reasons = []

        # --- 수급 (40) ---
        fs, ist = s.get("f_streak", 0) or 0, s.get("i_streak", 0) or 0
        f5amt = s.get("f_5d_amt_100m") or 0
        flow_pts += min(fs, CONFIG["flow_f_streak_cap"]) * 2.0   # 외국인 연속 순매수
        flow_pts += min(ist, CONFIG["flow_i_streak_cap"]) * 2.0  # 기관 연속 순매수
        if fs >= 3 and ist >= 3:
            flow_pts += CONFIG["flow_both_bonus"]  # 쌍끌이 매수
            reasons.append(f"외국인·기관 동반 순매수 ({fs}일/{ist}일)")
        elif fs >= 3:
            reasons.append(f"외국인 {fs}일 연속 순매수")
        elif ist >= 3:
            reasons.append(f"기관 {ist}일 연속 순매수")
        if f5amt >= CONFIG["flow_big_amt_100m"]:  # 5일 순매수 대금 기준
            flow_pts += 5
            reasons.append(f"외국인 5일 순매수 {f5amt:,.0f}억")

        # --- 밸류 (40) ---
        pbr, per, dvr = s.get("pbr"), s.get("per"), s.get("dvr")
        p_pbr = percentile_rank(pbrs, pbr)
        if p_pbr is not None:
            val_pts += (1 - p_pbr) * CONFIG["value_pbr_pts"]   # 저PBR일수록 ↑
            if pbr is not None and pbr < 0.8:
                reasons.append(f"PBR {pbr:.2f}배 (저평가)")
        p_dvr = percentile_rank(dvrs, dvr) if dvr is not None else None
        if p_dvr is not None:
            val_pts += p_dvr * CONFIG["value_dvr_pts"]         # 고배당일수록 ↑
            if dvr and dvr >= 3.0:
                reasons.append(f"배당수익률 {dvr:.1f}%")
        if per is not None and 0 < per < CONFIG["value_per_max"]:
            val_pts += 10
            if per < 8:
                reasons.append(f"PER {per:.1f}배")

        # --- 모멘텀 (10): 52주 신고가 근접 ---
        mom_pts = 0.0
        h52, price = s.get("h52"), s.get("price")
        if h52 and price and h52 > 0:
            near = price / h52
            s["near_52w_pct"] = round(near * 100, 1)
            for th, pts in CONFIG["mom_tiers"]:
                if near >= th:
                    mom_pts = pts
                    break
            if near >= 0.95 and fs >= 3:
                reasons.append(f"52주 신고가 근접({near*100:.0f}%) + 외인 매집")
            elif near >= 0.97:
                reasons.append(f"52주 신고가 근접 ({near*100:.0f}%)")

        # --- 공시 (20) ---
        for d in disc_by_company.get(s["name"], []):
            disc_pts += max(-20, min(20, d["score"]))
            arrow = {"positive": "+", "negative": "-", "watch": "·"}[d["sentiment"]]
            reasons.append(f"공시: {d['tag']} ({arrow})")
            s.setdefault("disclosures", []).append(
                {"tag": d["tag"], "sentiment": d["sentiment"], "title": d["title"]})
        disc_pts = max(-20.0, min(20.0, disc_pts))

        s["flow_score"] = round(flow_pts, 1)
        s["value_score"] = round(val_pts, 1)
        s["mom_score"] = round(mom_pts, 1)
        s["disc_score"] = round(disc_pts, 1)
        s["score"] = round(max(0.0, flow_pts + val_pts + mom_pts + disc_pts), 1)
        s["reasons"] = reasons
    return stocks


def pick_ideas(stocks, n=None, min_mktcap_100m=None):
    """종합점수 상위 n개 (시총 필터 적용, 마이너스 공시 종목 제외)."""
    n = n or CONFIG["idea_count"]
    min_mktcap_100m = min_mktcap_100m or CONFIG["idea_min_mktcap"]
    cands = [s for s in stocks
             if (s.get("mktcap_100m") or 0) >= min_mktcap_100m
             and s.get("disc_score", 0) >= 0
             and s.get("score", 0) > 0]
    cands.sort(key=lambda s: -s["score"])
    return cands[:n]


# ---------------------------------------------------------------------------
# 샘플 데이터 (오프라인 테스트/최초 미리보기용)
# ---------------------------------------------------------------------------

SAMPLE_STOCKS = [
    # (코드, 종목명, 시장, 가격, 등락률, 시총(억), PBR, PER, 배당, 외인연속, 기관연속, 외인5일억, 기관5일억)
    ("005930", "삼성전자", "KOSPI", 87400, 1.63, 5218000, 1.45, 13.2, 1.7, 6, 2, 4820, 310),
    ("000660", "SK하이닉스", "KOSPI", 292500, 2.81, 2129000, 2.31, 9.8, 0.5, 8, 4, 6120, 1840),
    ("005380", "현대차", "KOSPI", 264000, -0.38, 552000, 0.71, 5.4, 4.4, 4, 5, 890, 1120),
    ("000270", "기아", "KOSPI", 118500, 0.42, 471000, 0.78, 4.6, 5.5, 3, 4, 620, 480),
    ("105560", "KB금융", "KOSPI", 118200, 1.11, 462000, 0.62, 7.8, 4.1, 7, 3, 1340, 220),
    ("055550", "신한지주", "KOSPI", 66800, 0.75, 335000, 0.55, 6.9, 4.5, 5, 1, 760, 90),
    ("035420", "NAVER", "KOSPI", 231500, -1.07, 371000, 1.32, 21.4, 0.6, 0, 2, -410, 350),
    ("051910", "LG화학", "KOSPI", 342500, 3.16, 241000, 0.98, 28.1, 1.0, 2, 6, 380, 940),
    ("005490", "POSCO홀딩스", "KOSPI", 298000, 1.19, 252000, 0.51, 11.2, 3.4, 3, 0, 450, -120),
    ("015760", "한국전력", "KOSPI", 24950, 0.81, 160000, 0.42, 4.1, 0.0, 9, 2, 980, 150),
    ("034020", "두산에너빌리티", "KOSPI", 44800, 4.19, 287000, 3.10, 45.2, 0.0, 5, 5, 2210, 1650),
    ("012450", "한화에어로스페이스", "KOSPI", 912000, 2.53, 415000, 4.85, 22.7, 0.3, 4, 3, 1870, 890),
    ("086790", "하나금융지주", "KOSPI", 89100, 0.34, 251000, 0.58, 6.2, 4.8, 6, 4, 890, 410),
    ("096770", "SK이노베이션", "KOSPI", 128700, -0.62, 123000, 0.61, 0.0, 1.2, 1, 0, 120, -80),
    ("003550", "LG", "KOSPI", 82300, 0.24, 129000, 0.51, 6.8, 3.8, 2, 1, 180, 60),
    ("017670", "SK텔레콤", "KOSPI", 57200, 0.53, 122000, 0.95, 9.1, 6.2, 3, 2, 340, 120),
    ("030200", "KT", "KOSPI", 51900, 1.17, 129000, 0.68, 8.4, 4.3, 8, 5, 720, 380),
    ("035720", "카카오", "KOSPI", 48350, -0.92, 215000, 1.61, 38.5, 0.1, 0, 0, -230, -150),
    ("032830", "삼성생명", "KOSPI", 108500, 0.46, 217000, 0.41, 8.9, 4.2, 4, 2, 410, 130),
    ("009540", "HD한국조선해양", "KOSPI", 268500, 1.89, 190000, 1.72, 14.3, 0.7, 5, 3, 980, 520),
    ("247540", "에코프로비엠", "KOSDAQ", 108200, -1.55, 105800, 4.20, 0.0, 0.0, 0, 1, -310, 90),
    ("086520", "에코프로", "KOSDAQ", 62100, -0.80, 82700, 3.85, 19.8, 0.0, 1, 0, 110, -60),
    ("028300", "HLB", "KOSDAQ", 71400, 2.14, 93400, 8.91, 0.0, 0.0, 2, 1, 260, 70),
    ("196170", "알테오젠", "KOSDAQ", 342000, 1.03, 182000, 22.40, 88.0, 0.0, 3, 2, 520, 240),
    ("035760", "CJ ENM", "KOSDAQ", 71200, 0.71, 15600, 0.48, 0.0, 0.8, 4, 3, 90, 60),
]

SAMPLE_DISCLOSURES = [
    ("18:12", "KB금융", "주요사항보고서(자기주식취득결정)", "KOSPI", "자사주 매입", "positive", 10),
    ("17:48", "현대차", "주요사항보고서(자기주식소각결정)", "KOSPI", "자사주 소각", "positive", 15),
    ("17:05", "두산에너빌리티", "단일판매ㆍ공급계약체결", "KOSPI", "공급계약", "positive", 8),
    ("16:44", "샘플바이오", "유상증자결정", "KOSDAQ", "유상증자", "negative", -10),
    ("16:20", "CJ ENM", "임원ㆍ주요주주특정증권등소유상황보고서", "KOSDAQ", "내부자 지분변동", "watch", 0),
    ("15:58", "한국전력", "현금ㆍ현물배당결정", "KOSPI", "배당결정", "positive", 5),
]


def build_sample():
    random.seed(20260710)
    stocks = []
    for (code, name, mkt, price, chg, mv, pbr, per, dvr, fst, ist, f5, i5) in SAMPLE_STOCKS:
        stocks.append({
            "code": code, "name": name, "market": mkt, "price": price,
            "change_pct": chg, "mktcap_100m": mv, "volume": None,
            "pbr": pbr or None, "per": per or None, "dvr": dvr,
            "f_streak": fst, "i_streak": ist,
            "f_5d_amt_100m": f5, "i_5d_amt_100m": i5,
            "f_20d_amt_100m": round(f5 * random.uniform(1.5, 3.2), 0),
            "i_20d_amt_100m": round(i5 * random.uniform(1.2, 2.8), 0),
        })
    disclosures = [
        {"time": t, "company": c, "title": ti, "market": mk,
         "tag": tag, "sentiment": senti, "score": sc,
         "url": "https://dart.fss.or.kr/"}
        for (t, c, ti, mk, tag, senti, sc) in SAMPLE_DISCLOSURES
    ]
    _sects = ["IT", "금융", "산업재", "경기소비재", "건강관리", "소재",
              "커뮤니케이션", "유틸리티", "에너지", "필수소비재"]
    for i, s in enumerate(stocks):         # 미리보기용 업종
        s["sector"] = _sects[i % len(_sects)]
    for s in stocks:                       # 미리보기용 52주 최고가
        s["h52"] = round(s["price"] / random.choice((0.72, 0.85, 0.93, 0.96, 0.99)))
    stocks = score_stocks(stocks, disclosures)
    for s in stocks:                       # 미리보기용 전일 대비 변화량
        s["flow_delta"] = round(random.uniform(-6, 12), 1)
    ideas = pick_ideas(stocks, 5)
    for i, s in enumerate(ideas):          # 미리보기용 연속 선정일수
        s["idea_days"] = (1, 3, 1, 2, 5)[i % 5]
    # 미리보기용 성과 트래킹
    perf_records = []
    for i, day in enumerate(("2026-07-09", "2026-07-08", "2026-07-07",
                             "2026-07-06", "2026-07-03")):
        rets = [round(random.uniform(-2.5, 5.5), 2) for _ in range(5)]
        perf_records.append({
            "date": day, "avg_ret_pct": round(sum(rets) / 5, 2),
            "kospi_ret_pct": round(random.uniform(-1.0, 2.5), 2),
            "ideas": [{"code": s["code"], "name": s["name"], "entry": s["price"],
                       "ret_pct": rets[j]} for j, s in enumerate(ideas)]})
    wins = sum(1 for r in perf_records if r["avg_ret_pct"] > 0)
    beat = sum(1 for r in perf_records if r["avg_ret_pct"] > r["kospi_ret_pct"])
    performance = {"days": len(perf_records), "records": perf_records,
                   "summary": {"avg_ret_pct": round(sum(r["avg_ret_pct"] for r in perf_records) / len(perf_records), 2),
                               "win_rate_pct": round(wins / len(perf_records) * 100),
                               "beat_kospi_pct": round(beat / len(perf_records) * 100)}}
    # 미리보기용 지수 추이 (완만한 랜덤워크)
    trend, v = [], 3280.0
    for i in range(20):
        v = round(v * random.uniform(0.995, 1.012), 2)
        trend.append({"d": f"2026-06-{10+i:02d}", "v": v})
    trend.append({"d": "2026-07-10", "v": 3412.68})
    now = datetime.now(KST)
    return assemble(stocks, disclosures, ideas,
                    {"KOSPI": {"value": 3412.68, "change_pct": 0.87},
                     "KOSDAQ": {"value": 812.45, "change_pct": -0.21}},
                    now, sample=True, errors=[], performance=performance,
                    kospi_trend=trend)


# ---------------------------------------------------------------------------
# 조립 & 메인
# ---------------------------------------------------------------------------

def assemble(stocks, disclosures, ideas, indices, now, sample=False, errors=None,
             market_date=None, performance=None, kospi_trend=None):
    def slim(s):
        return {k: s.get(k) for k in (
            "code", "name", "market", "price", "change_pct", "mktcap_100m",
            "pbr", "per", "dvr", "f_streak", "i_streak",
            "f_5d_amt_100m", "i_5d_amt_100m", "f_20d_amt_100m", "i_20d_amt_100m",
            "flow_score", "value_score", "mom_score", "disc_score", "score",
            "reasons", "flow_delta", "idea_days", "near_52w_pct", "sector",
            "disclosures")}

    def compact(s):
        """전체 종목 목록용 축약 레코드 (검색·워치리스트에 사용)."""
        return {k: s.get(k) for k in (
            "code", "name", "market", "price", "change_pct", "mktcap_100m",
            "pbr", "per", "dvr", "f_streak", "i_streak", "f_5d_amt_100m",
            "flow_score", "value_score", "mom_score", "disc_score", "score",
            "flow_delta", "near_52w_pct", "reasons", "sector")}

    flow_rank = sorted(stocks, key=lambda s: -(s.get("flow_score") or 0))[:30]
    value_rank = sorted(stocks, key=lambda s: -(s.get("value_score") or 0))[:30]
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M KST"),
        "market_date": market_date or now.strftime("%Y-%m-%d"),
        "sample": sample,
        "indices": indices,
        "ideas": [slim(s) for s in ideas],
        "flow_scan": [slim(s) for s in flow_rank],
        "value_screen": [slim(s) for s in value_rank],
        "disclosures": disclosures[:60],
        "performance": performance or {"days": 0, "records": []},
        "kospi_trend": kospi_trend or [],
        "all_stocks": [compact(s) for s in
                       sorted(stocks, key=lambda s: -(s.get("mktcap_100m") or 0))],
        "universe_size": len(stocks),
        "errors": errors or [],
    }


def run_full(max_universe=None, out_path=None):
    errors = []
    now = datetime.now(KST)

    print("[0/5] 장 기준일 확인...")
    indices, market_date = fetch_indices()
    prev = load_previous(out_path or OUT_PATH)
    if is_holiday_rerun(prev, market_date):
        print(f"휴장일/중복 실행 감지 (장 기준일 {market_date} 동일) - 갱신 생략")
        return None

    print("[1/5] 유니버스 수집...")
    stocks = fetch_universe()
    if max_universe:
        stocks = stocks[:max_universe]
    if not stocks:
        raise SystemExit("유니버스 수집 실패 - 네트워크 환경을 확인하세요.")
    print(f"  → {len(stocks)}종목")

    print("[2/5] 펀더멘털 (PER/PBR/배당)...")
    for i, s in enumerate(stocks):
        try:
            s.update(fetch_fundamentals(s["code"]))
        except Exception as e:
            errors.append(f"fund {s['code']}: {e}")
        if i % 50 == 0:
            print(f"  ... {i}/{len(stocks)}")
        time.sleep(REQUEST_DELAY)

    print("[3/5] 수급 (외국인/기관)...")
    for i, s in enumerate(stocks):
        try:
            rows = fetch_investor_flows(s["code"])
            m = flow_metrics(rows, s.get("price"))
            if m:
                s.update(m)
        except Exception as e:
            errors.append(f"flow {s['code']}: {e}")
        if i % 50 == 0:
            print(f"  ... {i}/{len(stocks)}")
        time.sleep(REQUEST_DELAY)

    print("[3.5/5] 업종 매핑...")
    try:
        sector_map = fetch_sector_map(market_date)
        for s in stocks:
            if s["code"] in sector_map:
                s["sector"] = sector_map[s["code"]]
        print(f"  → {sum(1 for s in stocks if s.get('sector'))}종목 매핑")
    except Exception as e:
        errors.append(f"sector: {e}")

    print("[4/5] DART 공시...")
    raw = []
    api_key = os.environ.get("DART_API_KEY", "").strip()
    try:
        if api_key:
            raw = fetch_dart_openapi(api_key, days=3)
        if not raw:
            raw = fetch_dart_rss()
    except Exception as e:
        errors.append(f"dart: {e}")
    disclosures = build_disclosure_signals(raw)
    print(f"  → 공시 {len(raw)}건 중 시그널 {len(disclosures)}건")

    print("[5/5] 품질 검증/점수화...")
    fatal = validate_collection(stocks)
    if fatal:
        print("수집 품질 미달 - 갱신 중단 (직전 데이터 유지):", *fatal, sep="\n  ")
        sys.exit(2)

    stocks = score_stocks(stocks, disclosures)
    stocks = apply_flow_delta(stocks, prev)
    ideas = pick_ideas(stocks, 5)
    hist_dir = Path(out_path or OUT_PATH).parent / "history"
    ideas = apply_idea_streaks(ideas, load_history_idea_codes(hist_dir, market_date))
    performance = build_performance(hist_dir, stocks, indices, market_date)
    kospi_trend = build_index_trend(hist_dir, indices, market_date)

    if errors[:5]:
        print("경고:", *errors[:5], sep="\n  ")
    return assemble(stocks, disclosures, ideas, indices, now, errors=errors[:20],
                    market_date=market_date, performance=performance,
                    kospi_trend=kospi_trend)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="오프라인 샘플 데이터 생성")
    ap.add_argument("--max-universe", type=int, default=None)
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    out = Path(args.out)
    data = build_sample() if args.sample else run_full(args.max_universe, out_path=out)
    if data is None:          # 휴장일 → 갱신 없음 (정상 종료)
        return

    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    out.write_text(payload, encoding="utf-8")

    # 히스토리 축적 (실데이터만): history/YYYY-MM-DD.json
    if not data.get("sample"):
        hist = out.parent / "history" / f"{data['market_date']}.json"
        hist.parent.mkdir(parents=True, exist_ok=True)
        hist.write_text(payload, encoding="utf-8")
        print(f"히스토리 저장: {hist}")

    print(f"저장 완료: {out}  (장 기준일 {data['market_date']}, "
          f"아이디어 {len(data['ideas'])}건, 공시 시그널 {len(data['disclosures'])}건)")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
