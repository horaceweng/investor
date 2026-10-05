"""SEC EDGAR 存取: 帶快取的下載、申報清單、13F 持股明細解析。

SEC 要求每個請求在 User-Agent 標明聯絡方式 (姓名 + email)。為避免把個人 email 寫進原始碼/公開倉庫,
改由環境變數 SEC_USER_AGENT, 或本機檔案 data/config/sec_user_agent.txt (已被 .gitignore) 提供。
"""
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict

import requests

from investor import paths

SUBMISSIONS_TTL = 6 * 3600     # 申報清單會有新申報, 6 小時後重抓; 申報內容本身不會變, 永久快取


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    f = paths.CONFIG / "sec_user_agent.txt"
    if not ua and f.exists():
        ua = f.read_text().strip()
    if not ua:
        raise RuntimeError("請設定 SEC User-Agent: 環境變數 SEC_USER_AGENT, 或建立 data/config/sec_user_agent.txt, "
                           "內容如 'Your Name your@email.com' (SEC EDGAR 要求標明聯絡方式)")
    return ua


def fetch(url: str, max_age=None):
    """下載並快取; max_age(秒)給定時, 快取超過就重抓 (抓不到則退回舊快取)。失敗且無快取回傳 None。"""
    path = paths.SEC_CACHE / re.sub(r"\W+", "_", url)
    if path.exists() and (max_age is None or time.time() - path.stat().st_mtime < max_age):
        return path.read_bytes()
    for _ in range(4):
        r = requests.get(url, headers={"User-Agent": user_agent()}, timeout=30)
        if r.status_code == 200:
            paths.ensure_parent(path).write_bytes(r.content)
            time.sleep(0.15)                       # SEC 限制每秒 10 次請求
            return r.content
        time.sleep(1.5)
    return path.read_bytes() if path.exists() else None


def submissions(cik: int) -> dict:
    return json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", SUBMISSIONS_TTL))


def find_filing(cik: int, quarter: str):
    """回傳該季 13F-HR 的 accession number; 沒有則 None。"""
    f = submissions(cik)["filings"]["recent"]
    for form, rd, acc in zip(f["form"], f["reportDate"], f["accessionNumber"]):
        if form == "13F-HR" and rd == quarter:
            return acc
    return None


def holdings(cik: int, acc: str):
    """解析一份 13F 的持股明細: {cusip: [股數, 發行人名稱]}; 選擇權與非股票(SH)部位略過。失敗回傳 None。"""
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
    idx = fetch(base + "/index.json")
    if not idx:
        return None
    items = json.loads(idx)["directory"]["item"]
    xmls = [i for i in items if i["name"].lower().endswith(".xml") and "primary_doc" not in i["name"].lower()]
    if not xmls:
        return None
    xmls.sort(key=lambda i: int(i.get("size") or 0), reverse=True)
    data = fetch(f"{base}/{xmls[0]['name']}")
    if not data:
        return None
    out = defaultdict(lambda: [0.0, ""])
    for el in ET.fromstring(data).iter():
        if not el.tag.endswith("infoTable"):
            continue
        d = {c.tag.split("}")[-1]: c for c in el.iter()}
        if "putCall" in d:
            continue
        sh = d.get("sshPrnamt")
        if sh is None or d["sshPrnamtType"].text != "SH":
            continue
        cusip = d["cusip"].text.strip().upper()
        out[cusip][0] += float(sh.text)
        out[cusip][1] = d["nameOfIssuer"].text.strip()
    return out
