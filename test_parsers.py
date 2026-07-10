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
            "i_5d_amt_100m": 0}
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
