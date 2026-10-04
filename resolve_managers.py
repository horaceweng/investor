"""把經理人名稱解析成 SEC CIK，只保留有 QUARTER 13F-HR 申報者。"""
import csv, sys, time
import requests

from sec_ua import user_agent

QUARTER = "2026-06-30"

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

def get(url, **kw):
    for _ in range(3):
        r = requests.get(url, headers={"User-Agent": user_agent()}, timeout=20, **kw)
        if r.status_code == 200:
            return r
        time.sleep(1)
    return None

out, missing = [], []
for name in NAMES:
    r = get("https://efts.sec.gov/LATEST/search-index", params={"keysTyped": name})
    picked = None
    for h in (r.json()["hits"]["hits"][:6] if r else []):
        cik = int(h["_id"])
        s = get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        time.sleep(0.12)
        if not s:
            continue
        f = s.json()["filings"]["recent"]
        if any(t == "13F-HR" and d == QUARTER for t, d in zip(f["form"], f["reportDate"])):
            picked = (cik, s.json()["name"]); break
    if picked:
        out.append((name, picked[0], picked[1])); print(f"OK   {name:38} -> {picked[1]} ({picked[0]})")
    else:
        missing.append(name); print(f"MISS {name}")

with open("managers.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["manager", "cik", "sec_name"]); w.writerows(out)
print(f"\nresolved {len(out)} / {len(NAMES)}; missing: {missing}")
