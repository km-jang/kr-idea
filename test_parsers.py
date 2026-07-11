# -*- coding: utf-8 -*-
"""파서/점수화 로직 오프라인 테스트 (네트워크 불필요).

실제 응답 구조를 본뜬 픽스처로 검증한다.
실행: python test_parsers.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect  # noqa: E402


# ---------------------------------------------------------------------------
# 1) marketValue API 항목 파싱
# ---------------------------------------------------------------------------

def test_parse_market_value_item():
    it = {
        "itemCode": "005930", "stockName": "삼성전자",
        "closePrice": "87,400", "compareToPreviousClosePrice": "1,400",
        "fluctuationsRatio": "1.63",
        "compareToPreviousPrice": {"code": "2", "text": "상승"},
        "marketValue": "5,218,000", "accumulatedTradingVolume": "12,345,678",
    }
    s = collect.parse_market_value_item(it, "KOSPI")
    assert s["code"] == "005930"
    assert s["name"] == "삼성전자"
    assert s["price"] == 87400.0
    assert s["change_pct"] == 1.63
    assert s["mktcap_100m"] == 5218000.0


def test_parse_market_value_item_down_sign():
    it = {"itemCode": "035420", "stockName": "NAVER", "closePrice": "231,500",
          "fluctuationsRatio": "1.07",
          "compareToPreviousPrice": {"code": "5", "text": "하락"},
          "marketValue": "371,000"}
    s = collect.parse_market_value_item(it, "KOSPI")
    assert s["change_pct"] == -1.07  # 하락코드(5)면 부호 보정


def test_parse_market_value_item_missing():
    assert collect.parse_market_value_item({}, "KOSPI") is None


# ---------------------------------------------------------------------------
# 2) integration API (PER/PBR/배당) 파싱
# ---------------------------------------------------------------------------

def test_parse_integration():
    data = {"totalInfos": [
        {"code": "marketValue", "key": "시가총액", "value": "521조 8,000억"},
        {"code": "per", "key": "PER", "value": "13.20배"},
        {"code": "pbr", "key": "PBR", "value": "1.45배"},
        {"code": "eps", "key": "EPS", "value": "6,621원"},
        {"code": "bps", "key": "BPS", "value": "60,276원"},
        {"code": "dvr", "key": "배당수익률", "value": "1.66%"},
    ]}
    f = collect.parse_integration(data)
    assert f["per"] == 13.20
    assert f["pbr"] == 1.45
    assert f["dvr"] == 1.66
    assert f["eps"] == 6621.0


def test_parse_integration_key_fallback():
    # code 필드가 낯선 값이어도 key 텍스트로 복원
    data = {"totalInfos": [
        {"code": "xx1", "key": "PER(배)", "value": "9.87"},
        {"code": "xx2", "key": "PBR(배)", "value": "0.71"},
        {"code": "xx3", "key": "배당수익률", "value": "4.40%"},
    ]}
    f = collect.parse_integration(data)
    assert f["per"] == 9.87
    assert f["pbr"] == 0.71
    assert f["dvr"] == 4.40


def test_parse_integration_empty():
    f = collect.parse_integration({})
    assert f["per"] is None and f["pbr"] is None


# ---------------------------------------------------------------------------
# 3) frgn 페이지 (외국인/기관 순매매) 파싱
# ---------------------------------------------------------------------------

FRGN_HTML = """
<html><body>
<table summary="외국인 기관 순매매 거래량에 관한표이며 날짜별로 정보를 제공합니다.">
<tr><th>날짜</th><th>종가</th><th>전일비</th><th>등락률</th><th>거래량</th>
<th>기관</th><th>외국인</th><th>보유주수</th><th>보유율</th></tr>
<tr><td class="tc">2026.07.09</td><td class="num">87,400</td>
<td class="num"><img src="up.gif">1,400</td><td class="num">+1.63%</td>
<td class="num">12,345,678</td><td class="num">+120,000</td>
<td class="num">+1,530,000</td><td class="num">3,700,000,000</td>
<td class="num">62.01%</td></tr>
<tr><td class="tc">2026.07.08</td><td class="num">86,000</td>
<td class="num">500</td><td class="num">+0.58%</td>
<td class="num">10,000,000</td><td class="num">-50,000</td>
<td class="num">+900,000</td><td class="num">3,698,470,000</td>
<td class="num">61.98%</td></tr>
<tr><td class="tc">2026.07.07</td><td class="num">85,500</td>
<td class="num">300</td><td class="num">-0.35%</td>
<td class="num">9,000,000</td><td class="num">+70,000</td>
<td class="num">+400,000</td><td class="num">3,697,570,000</td>
<td class="num">61.97%</td></tr>
<tr><td class="tc">2026.07.04</td><td class="num">85,800</td>
<td class="num">200</td><td class="num">+0.23%</td>
<td class="num">8,000,000</td><td class="num">+10,000</td>
<td class="num">-200,000</td><td class="num">3,697,170,000</td>
<td class="num">61.96%</td></tr>
</table>
</body></html>
"""


def test_parse_frgn_html():
    rows = collect.parse_frgn_html(FRGN_HTML)
    assert len(rows) == 4
    assert rows[0]["date"] == "2026.07.09"
    assert rows[0]["close"] == 87400.0
    assert rows[0]["inst"] == 120000.0
    assert rows[0]["frgn"] == 1530000.0
    assert rows[1]["inst"] == -50000.0


def test_flow_metrics_streak():
    rows = collect.parse_frgn_html(FRGN_HTML)
    m = collect.flow_metrics(rows, price=87400)
    assert m["f_streak"] == 3          # 외국인 3일 연속 순매수 후 4일째 매도
    assert m["i_streak"] == 1          # 기관 1일 순매수 후 매도
    assert m["f_5d"] == 1530000 + 900000 + 400000 - 200000
    # 금액(억): 순매수량 × 가격 / 1e8
    assert abs(m["f_5d_amt_100m"] - (m["f_5d"] * 87400 / 1e8)) < 0.11


def test_flow_metrics_empty():
    assert collect.flow_metrics([]) is None


# ---------------------------------------------------------------------------
# 4) DART RSS 파싱 & 공시 분류
# ---------------------------------------------------------------------------

DART_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>DART</title>
<item><title>(유가)현대차 - 주요사항보고서(자기주식소각결정)</title>
<link>https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260709000123</link>
<pubDate>Thu, 09 Jul 2026 17:48:00 +0900</pubDate></item>
<item><title>(코스닥)샘플바이오 - 유상증자결정</title>
<link>https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260709000456</link>
<pubDate>Thu, 09 Jul 2026 16:44:00 +0900</pubDate></item>
<item><title>(유가)신한지주 - 기업설명회(IR)개최(안내공시)</title>
<link>https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260709000789</link>
<pubDate>Thu, 09 Jul 2026 15:00:00 +0900</pubDate></item>
</channel></rss>"""


def test_parse_dart_rss():
    items = collect.parse_dart_rss(DART_RSS)
    assert len(items) == 3
    assert items[0]["company"] == "현대차"
    assert items[0]["market"] == "KOSPI"
    assert "자기주식소각결정" in items[0]["title"]
    assert items[1]["market"] == "KOSDAQ"


def test_classify_disclosure():
    assert collect.classify_disclosure("주요사항보고서(자기주식소각결정)")[1] == "positive"
    assert collect.classify_disclosure("유상증자결정")[1] == "negative"
    assert collect.classify_disclosure("임원ㆍ주요주주특정증권등소유상황보고서")[1] == "watch"
    assert collect.classify_disclosure("기업설명회(IR)개최") is None


def test_build_disclosure_signals():
    items = collect.parse_dart_rss(DART_RSS)
    sig = collect.build_disclosure_signals(items)
    assert len(sig) == 2                      # IR 공시는 제외
    assert sig[0]["tag"] == "자사주 소각"
    assert sig[1]["sentiment"] == "negative"


# ---------------------------------------------------------------------------
# 5) 점수화 & 아이디어 선정
# ---------------------------------------------------------------------------

def _mk(code, name, **kw):
    base = {"code": code, "name": name, "market": "KOSPI", "price": 10000,
            "change_pct": 0.0, "mktcap_100m": 50000, "pbr": 1.0, "per": 10.0,
            "dvr": 2.0, "f_streak": 0, "i_streak": 0, "f_5d_amt_100m": 0,
            "i_5d_amt_100m": 0, "h52": None}
    base.update(kw)
    return base


def test_score_and_pick():
    stocks = [
        _mk("A00001", "좋은수급", f_streak=8, i_streak=4, f_5d_amt_100m=500),
        _mk("A00002", "좋은밸류", pbr=0.4, per=4.0, dvr=5.5),
        _mk("A00003", "평범주식"),
        _mk("A00004", "작은종목", f_streak=9, mktcap_100m=500),   # 시총 미달
        _mk("A00005", "악재공시", pbr=0.3, per=3.0, dvr=6.0),
    ]
    disc = [{"company": "악재공시", "title": "유상증자결정", "tag": "유상증자",
             "sentiment": "negative", "score": -10, "time": "", "url": "", "market": ""}]
    scored = collect.score_stocks(stocks, disc)
    by = {s["name"]: s for s in scored}

    assert by["좋은수급"]["flow_score"] > by["평범주식"]["flow_score"]
    assert by["좋은밸류"]["value_score"] > by["평범주식"]["value_score"]
    assert by["악재공시"]["disc_score"] < 0
    assert any("연속 순매수" in r or "동반 순매수" in r for r in by["좋은수급"]["reasons"])

    ideas = collect.pick_ideas(scored, n=3)
    names = [s["name"] for s in ideas]
    assert "작은종목" not in names          # 시총 필터
    assert "악재공시" not in names          # 마이너스 공시 제외
    assert names[0] in ("좋은수급", "좋은밸류")


def test_sample_build_schema():
    data = collect.build_sample()
    for key in ("generated_at", "indices", "ideas", "flow_scan",
                "value_screen", "disclosures", "universe_size", "all_stocks"):
        assert key in data, key
    assert data["sample"] is True
    assert 1 <= len(data["ideas"]) <= 5
    assert len(data["flow_scan"]) > 0
    assert all(s.get("score") is not None for s in data["ideas"])
    # JSON 직렬화 가능해야 함
    json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------------------

def test_to_num():
    assert collect.to_num("74,300") == 74300.0
    assert collect.to_num("2.15%") == 2.15
    assert collect.to_num("13.2배") == 13.2
    assert collect.to_num("-") is None
    assert collect.to_num(None, 0) == 0
    assert collect.to_num("+1,400") == 1400.0



# ---------------------------------------------------------------------------
# 6) 품질 가드 & 휴장일 스킵
# ---------------------------------------------------------------------------

def _universe(n, price=True, days=True, pbr=True):
    out = []
    for i in range(n):
        s = {"code": f"{i:06d}", "name": f"종목{i}", "price": 10000 if price else None}
        if days: s["days"] = 20
        if pbr: s["pbr"] = 1.0
        out.append(s)
    return out


def test_validate_ok():
    assert collect.validate_collection(_universe(300)) == []


def test_validate_small_universe():
    fatal = collect.validate_collection(_universe(50))
    assert fatal and "유니버스" in fatal[0]


def test_validate_missing_flows():
    stocks = _universe(300, days=False)
    fatal = collect.validate_collection(stocks)
    assert any("수급" in f for f in fatal)


def test_validate_missing_fundamentals():
    stocks = _universe(300, pbr=False)
    fatal = collect.validate_collection(stocks)
    assert any("펀더멘털" in f for f in fatal)


def test_holiday_skip():
    prev = {"sample": False, "market_date": "2026-07-10"}
    assert collect.is_holiday_rerun(prev, "2026-07-10") is True    # 같은 장 기준일 → 스킵
    assert collect.is_holiday_rerun(prev, "2026-07-11") is False   # 새 거래일 → 진행
    assert collect.is_holiday_rerun(None, "2026-07-10") is False   # 이전 데이터 없음 → 진행
    assert collect.is_holiday_rerun(prev, None) is False           # 기준일 미확인 → 진행(안전)


def test_load_previous(tmp_path=None):
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "data.json")
        # 샘플 데이터는 None 취급
        open(p, "w").write(json.dumps({"sample": True, "market_date": "2026-07-09"}))
        assert collect.load_previous(p) is None
        open(p, "w").write(json.dumps({"sample": False, "market_date": "2026-07-09"}))
        assert collect.load_previous(p)["market_date"] == "2026-07-09"
        assert collect.load_previous(os.path.join(td, "없는파일.json")) is None


def test_assemble_market_date():
    data = collect.build_sample()
    assert "market_date" in data


def test_classify_new_keywords():
    assert collect.classify_disclosure("주요사항보고서(자기주식처분결정)")[1] == "negative"
    assert collect.classify_disclosure("소송등의제기ㆍ신청")[1] == "negative"
    assert collect.classify_disclosure("회사합병결정")[1] == "watch"
    assert collect.classify_disclosure("타법인주식및출자증권취득결정")[1] == "watch"


def test_flow_delta():
    stocks = [{"code": "A", "flow_score": 30.0}, {"code": "B", "flow_score": 10.0},
              {"code": "C", "flow_score": 5.0}]
    prev = {"all_stocks": [{"code": "A", "flow_score": 18.0}, {"code": "B", "flow_score": 12.0}]}
    out = collect.apply_flow_delta(stocks, prev)
    assert out[0]["flow_delta"] == 12.0      # 급등
    assert out[1]["flow_delta"] == -2.0
    assert "flow_delta" not in out[2]        # 전일 데이터 없는 신규 종목
    assert collect.apply_flow_delta(stocks, None) == stocks  # 이전 데이터 없으면 그대로


def test_flow_delta_old_format():
    # 구버전 data.json (all_stocks 없음) → flow_scan 폴백
    stocks = [{"code": "A", "flow_score": 20.0}]
    prev = {"flow_scan": [{"code": "A", "flow_score": 15.0}], "ideas": []}
    assert collect.apply_flow_delta(stocks, prev)[0]["flow_delta"] == 5.0


def test_idea_streaks():
    ideas = [{"code": "A"}, {"code": "B"}, {"code": "C"}]
    past = [{"A", "B"}, {"A"}, {"A", "C"}]   # 최신순: 어제, 그제, 그끄제
    out = collect.apply_idea_streaks(ideas, past)
    by = {s["code"]: s["idea_days"] for s in out}
    assert by["A"] == 4                       # 오늘 포함 4일 연속
    assert by["B"] == 2                       # 어제부터
    assert by["C"] == 1                       # 그끄제엔 있었지만 연속 아님 → NEW
    assert collect.apply_idea_streaks([{"code":"X"}], [])[0]["idea_days"] == 1


def test_history_loader():
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        for day, codes in (("2026-07-08", ["A"]), ("2026-07-09", ["A","B"]),
                           ("2026-07-10", ["B"])):
            open(os.path.join(td, day+".json"), "w").write(
                json.dumps({"ideas": [{"code": c} for c in codes]}))
        # 오늘(7/10) 이전 것만, 최신순
        sets = collect.load_history_idea_codes(td, "2026-07-10")
        assert sets == [{"A","B"}, {"A"}]
        assert collect.load_history_idea_codes(td, None) or True  # 예외 없이 동작
        assert collect.load_history_idea_codes("/없는폴더", "2026-07-10") == []


def test_parse_integration_h52():
    data = {"totalInfos": [
        {"code": "highPriceOf52Weeks", "key": "52주 최고", "value": "89,000"},
        {"code": "per", "key": "PER", "value": "10배"}]}
    assert collect.parse_integration(data)["h52"] == 89000.0


def test_momentum_score():
    near = _mk("M00001", "신고가주", h52=10200)     # 10000/10200 = 98%
    far = _mk("M00002", "저점주", h52=20000)        # 50%
    scored = collect.score_stocks([near, far], [])
    by = {s["name"]: s for s in scored}
    assert by["신고가주"]["mom_score"] == 10.0
    assert by["저점주"]["mom_score"] == 0.0
    assert by["신고가주"]["score"] > by["저점주"]["score"]
    assert any("신고가" in r for r in by["신고가주"]["reasons"])
    assert by["신고가주"]["near_52w_pct"] == 98.0


def test_build_performance():
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        # 이틀 전: A를 10000원에 선정 / KOSPI 7000
        open(os.path.join(td, "2026-07-08.json"), "w").write(json.dumps({
            "ideas": [{"code": "A", "name": "가", "price": 10000}],
            "indices": {"KOSPI": {"value": 7000.0}}}))
        # 어제: A(10500), B(20000) 선정 / KOSPI 7100
        open(os.path.join(td, "2026-07-09.json"), "w").write(json.dumps({
            "ideas": [{"code": "A", "name": "가", "price": 10500},
                      {"code": "B", "name": "나", "price": 20000}],
            "indices": {"KOSPI": {"value": 7100.0}}}))
        # 오늘 가격: A=11000(+10%/+4.76%), B=19000(-5%)
        stocks = [{"code": "A", "price": 11000}, {"code": "B", "price": 19000}]
        indices = {"KOSPI": {"value": 7200.0}}
        perf = collect.build_performance(td, stocks, indices, "2026-07-10")
        assert perf["days"] == 2
        r_by = {r["date"]: r for r in perf["records"]}
        assert r_by["2026-07-08"]["avg_ret_pct"] == 10.0
        assert abs(r_by["2026-07-09"]["avg_ret_pct"] - (-0.12)) < 0.02  # (4.76-5)/2
        assert abs(r_by["2026-07-08"]["kospi_ret_pct"] - 2.86) < 0.01
        assert perf["summary"]["win_rate_pct"] == 50
        # 오늘 날짜 파일은 제외되는지
        open(os.path.join(td, "2026-07-10.json"), "w").write(json.dumps({
            "ideas": [{"code": "A", "name": "가", "price": 11000}],
            "indices": {"KOSPI": {"value": 7200.0}}}))
        perf2 = collect.build_performance(td, stocks, indices, "2026-07-10")
        assert perf2["days"] == 2


def test_build_performance_empty():
    perf = collect.build_performance("/없는폴더", [], {}, "2026-07-10")
    assert perf["days"] == 0


def test_index_trend():
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        for day, v in (("2026-07-08", 7000.0), ("2026-07-09", 7100.0),
                       ("2026-07-10", 7200.0)):
            open(os.path.join(td, day+".json"), "w").write(json.dumps(
                {"indices": {"KOSPI": {"value": v}}}))
        pts = collect.build_index_trend(td, {"KOSPI": {"value": 7300.0}}, "2026-07-10")
        assert [p["v"] for p in pts] == [7000.0, 7100.0, 7300.0]  # 오늘 파일 제외 + 현재값 추가
        assert pts[-1]["d"] == "2026-07-10"
        assert collect.build_index_trend("/없는폴더", {}, None) == []


def test_watchlist_parse_and_lines():
    import notify
    txt = """# 주석
005930 삼성전자
000660  # 하이닉스
잘못된줄
005930  # 중복
"""
    codes = notify.parse_watchlist(txt)
    assert codes == ["005930", "000660"]
    data = {"all_stocks": [
        {"code": "005930", "name": "삼성전자", "price": 87400, "change_pct": 1.6, "f_streak": 6},
        {"code": "000660", "name": "SK하이닉스", "price": 292500, "change_pct": -0.5, "f_streak": 0}]}
    lines = notify.watchlist_lines(data, codes)
    assert len(lines) == 2
    assert "삼성전자" in lines[0] and "외인6일" in lines[0]
    assert "▼0.5%" in lines[1]
    assert notify.watchlist_lines(data, []) == []


def test_evening_message():
    import notify
    data = {"market_date": "2026-07-10", "sample": False,
            "indices": {"KOSPI": {"value": 7475.94, "change_pct": 2.52},
                        "KOSDAQ": {"value": 837.43, "change_pct": 5.47}},
            "ideas": [{"code": "A", "name": "기업은행", "idea_days": 1, "score": 60,
                       "reasons": []}],
            "all_stocks": []}
    msg = notify.build_evening_message(data)
    assert "마감 요약" in msg and "기업은행" in msg and "🆕" in msg
    assert len(msg) < 4096


def test_config_wiring():
    # CONFIG 값이 실제로 산식에 반영되는지 (idea_count)
    old = collect.CONFIG["idea_count"]
    try:
        collect.CONFIG["idea_count"] = 2
        stocks = collect.score_stocks(
            [_mk(f"C{i:05d}", f"주식{i}", f_streak=i) for i in range(6)], [])
        assert len(collect.pick_ideas(stocks)) == 2
    finally:
        collect.CONFIG["idea_count"] = old


def test_parse_sector_items():
    data = {"list": [
        {"CMP_CD": "005930", "CMP_KOR": "삼성전자", "SEC_NM_KOR": "IT"},
        {"CMP_CD": "105560", "CMP_KOR": "KB금융"},          # SEC 누락 → 폴백
        {"CMP_CD": "", "CMP_KOR": "빈코드"}]}
    items = collect.parse_sector_items(data, "금융")
    assert ("005930", "IT") in items
    assert ("105560", "금융") in items
    assert len(items) == 2
    assert collect.parse_sector_items({}, "x") == []
    assert collect.parse_sector_items(None, "x") == []


def test_weekly_message():
    import notify
    data = {"kospi_trend": [{"d": "2026-07-07", "v": 7300.0},
                            {"d": "2026-07-10", "v": 7475.94}],
            "performance": {"days": 3, "summary": {
                "avg_ret_pct": 1.58, "win_rate_pct": 100, "beat_kospi_pct": 60},
                "records": [
                  {"date": "2026-07-09", "avg_ret_pct": 0.75, "kospi_ret_pct": 2.22,
                   "ideas": [{"name": "현대차", "ret_pct": 1.6}]},
                  {"date": "2026-07-08", "avg_ret_pct": 3.04, "kospi_ret_pct": 0.47,
                   "ideas": [{"name": "현대차", "ret_pct": 3.6}]}]}}
    msg = notify.build_weekly_message(data)
    assert "주간 결산" in msg and "성적표" in msg
    assert "1.58" in msg and "최다 선정: 현대차(2회)" in msg
    assert len(msg) < 4096
    # 데이터 없을 때도 안전
    msg2 = notify.build_weekly_message({"performance": {"days": 0}})
    assert "쌓이는 중" in msg2


def test_stooq_csv_parse():
    import notify
    csv = """Date,Open,High,Low,Close,Volume
2026-07-08,6100,6150,6080,6120,0
2026-07-09,6120,6200,6110,6180,0
2026-07-10,6180,6260,6170,6250,0"""
    val, chg = notify.parse_stooq_csv(csv)
    assert val == 6250.0
    assert abs(chg - 1.13) < 0.01
    assert notify.parse_stooq_csv("Date,Open\n") == (None, None)
    assert notify.parse_stooq_csv("") == (None, None)


def test_us_mood_wording():
    import notify
    assert "강세" in notify.us_mood_line({"나스닥": 2.1})
    assert "보수적" in notify.us_mood_line({"나스닥": -2.0})
    assert "원화 약세" in notify.us_mood_line({"환율": 0.8})
    assert "반도체" in notify.us_mood_line({"나스닥": 0.2, "반도체SOX": 3.5})
    assert notify.us_mood_line({"나스닥": 0.3}) == ""     # 평온한 날은 침묵


def test_gap_signals():
    import notify
    fake = {"nvda.us": (900, 5.2), "tsla.us": (300, -4.1), "aapl.us": (230, 0.5),
            "mu.us": (None, None), "amd.us": (150, 1.0), "lly.us": (800, 2.9),
            "avgo.us": (1700, 0.1)}
    lines = notify.gap_signal_lines(fetch=lambda s: fake.get(s, (None, None)))
    assert len(lines) == 2                     # ±3% 이상만 (엔비디아, 테슬라)
    assert any("엔비디아" in l and "▲5.2%" in l and "주목" in l for l in lines)
    assert any("테슬라" in l and "약세 주의" in l for l in lines)
    assert len(notify.gap_signal_lines(fetch=lambda s: fake.get(s, (None, None)),
                                       threshold=5.0)) == 1


def test_us_block_failsafe():
    import notify
    old = notify.fetch_stooq_change
    try:
        notify.fetch_stooq_change = lambda s, days=10: (None, None)
        assert notify.us_market_block() == []
        data = {"market_date": "2026-07-10", "sample": False,
                "indices": {"KOSPI": {"value": 7475.94, "change_pct": 2.52},
                            "KOSDAQ": {"value": 837.43, "change_pct": 5.47}},
                "ideas": [], "disclosures": [], "all_stocks": []}
        msg = notify.build_message(data)
        assert "아이디어 브리핑" in msg and "미국장" not in msg
    finally:
        notify.fetch_stooq_change = old


def test_yahoo_chart_parse():
    import notify
    data = {"chart": {"result": [{"indicators": {"quote": [{
        "close": [6100.0, None, 6180.0, 6250.0]}]}}]}}
    val, chg = notify.parse_yahoo_chart(data)
    assert val == 6250.0
    assert abs(chg - 1.13) < 0.01
    assert notify.parse_yahoo_chart({}) == (None, None)
    assert notify.parse_yahoo_chart({"chart": {"result": []}}) == (None, None)


def test_us_fetch_fallback_chain():
    import notify
    old_s, old_y = notify._stooq, notify._yahoo
    try:
        notify._stooq = lambda s, days=10: (None, None)
        notify._yahoo = lambda s: (6250.0, 1.1) if s == "^GSPC" else (None, None)
        assert notify.fetch_stooq_change("^spx") == (6250.0, 1.1)
        notify._yahoo = lambda s: (None, None)
        assert notify.fetch_stooq_change("^spx") == (None, None)
    finally:
        notify._stooq, notify._yahoo = old_s, old_y

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
