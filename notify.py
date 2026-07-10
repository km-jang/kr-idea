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
SITE_URL = "https://km-jang.github.io/kr-idea/"


def fmt_num(v, d=2):
    if v is None:
        return "-"
    return f"{v:,.{d}f}".rstrip("0").rstrip(".") if d else f"{v:,.0f}"


def arrow(pct):
    if pct is None:
        return ""
    return "▲" if pct > 0 else ("▼" if pct < 0 else "-")


def build_message(data):
    """data.json → 텔레그램 메시지 (HTML 포맷)."""
    e = lambda s: html.escape(str(s or ""))
    md = (data.get("market_date") or "").replace("-", ".")
    lines = [f"📊 <b>국내장 아이디어 브리핑</b>  <i>({e(md)} 장 마감 기준)</i>", ""]

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
    lines.append(f'📈 <a href="{SITE_URL}">대시보드 전체 보기</a>')
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

    msg = build_message(data)

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
