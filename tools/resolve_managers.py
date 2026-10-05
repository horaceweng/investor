#!/usr/bin/env python3
"""維護工具: 把經理人名稱解析成 SEC CIK, 只保留「有指定季度 13F-HR 申報」者。

用途: 新增或更新 investor/superinvestors/managers.csv 的候選名單。**不會直接覆蓋** managers.csv
(那份名單經過人工挑選), 結果寫到 data/exports/managers_resolved.csv, 請審閱後再決定是否取代。

用法: .venv/bin/python tools/resolve_managers.py [--quarter 2026-06-30]
"""
import argparse
import csv
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from investor import paths  # noqa: E402
from investor.data_sources import sec  # noqa: E402

NAMES = """Berkshire Hathaway|Pershing Square Capital|Scion Asset Management|Himalaya Capital|Duquesne Family Office
Third Point LLC|Greenlight Capital Inc|Baupost Group|Dalal Street LLC|Akre Capital Management|Gardner Russo & Quinn
Fairholme Capital|Southeastern Asset Management|Davis Selected Advisers|Harris Associates|Ruane Cunniff
Icahn Carl|TCI Fund Management|Appaloosa LP|Omega Advisors|Fairfax Financial|Markel Group|Daily Journal Corp
Gates Foundation Trust|Tweedy Browne|Dodge & Cox|Maverick Capital|Wedgewood Partners|Weitz Investment Management
Aquamarine Capital Management|Brave Warrior Advisors|Yacktman Asset Management|First Eagle Investment|Oaktree Capital Management
Polen Capital|Sarissa Capital|Jana Partners|Trian Fund Management|ValueAct Capital|Elliott Investment Management
Pzena Investment Management|Hotchkis & Wiley|Cooke & Bieler|Diamond Hill Capital|Mairs & Power|Torray LLC
Royce & Associates|Third Avenue Management|Donald Smith & Co|LSV Asset Management|Miller Value Partners
Gotham Asset Management|GAMCO Investors|Ariel Investments|Clarkston Capital|Chou Associates|Cannell Capital
First Pacific Advisors|ShawSpring Partners|Greenlea Lane Capital|Jensen Investment Management|Kahn Brothers
Bares Capital Management|Vulcan Value Partners|Sound Shore Management|Smead Capital Management|AltaRock Partners
Lloyd Miller|Prescott General Partners|Private Capital Management|Greenhaven Associates|Semper Augustus
Dorsey Asset Management|AKO Capital|Lone Pine Capital|Whale Rock Capital|Viking Global Investors|Glenview Capital
Abrams Capital Management|Mittleman Brothers|Cascade Investment|Hound Partners|Barrow Hanley Mewhinney|Starboard Value
Eagle Capital Management|GMO Grantham Mayo|Giverny Capital|Kerrisdale Capital|Egerton Capital|Lindsell Train
Pabrai Investment Funds|Meridian Contrarian|Mackenzie Financial|Burgundy Asset Management|Artisan Partners|Sprott Inc
Hennessy Advisors|Bridges Investment Management|Tocqueville Asset Management|Boston Partners|Madison Investment Holdings""".replace("\n", "|").split("|")


def search(name: str):
    """SEC 的名稱搜尋 (typeahead), 回傳候選的 (cik)。"""
    r = requests.get("https://efts.sec.gov/LATEST/search-index", params={"keysTyped": name},
                     headers={"User-Agent": sec.user_agent()}, timeout=20)
    return [int(h["_id"]) for h in (r.json()["hits"]["hits"][:6] if r.ok else [])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", default="2026-06-30", help="只保留在這一季有 13F-HR 申報的經理人")
    args = ap.parse_args()
    out, missing = [], []
    for name in NAMES:
        picked = None
        for cik in search(name):
            time.sleep(0.12)
            try:
                s = sec.submissions(cik)
            except Exception:
                continue
            f = s["filings"]["recent"]
            if any(t == "13F-HR" and d == args.quarter for t, d in zip(f["form"], f["reportDate"])):
                picked = (cik, s["name"])
                break
        if picked:
            out.append((name, *picked))
            print(f"OK   {name:38} -> {picked[1]} ({picked[0]})")
        else:
            missing.append(name)
            print(f"MISS {name}")

    dest = paths.ensure_parent(paths.EXPORTS / "managers_resolved.csv")
    with open(dest, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["manager", "cik", "sec_name"])
        w.writerows(out)
    print(f"\n解析 {len(out)} / {len(NAMES)}; 找不到: {missing}\n已寫入 {dest}\n"
          f"審閱後若要採用: cp {dest} {paths.MANAGERS_CSV}")


if __name__ == "__main__":
    main()
