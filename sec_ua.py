"""SEC EDGAR 要求每個請求在 User-Agent 標明聯絡方式 (姓名 + email)。為避免把個人 email 寫進原始碼/公開倉庫,
改由環境變數 SEC_USER_AGENT, 或本機檔案 data/sec_user_agent.txt (已被 .gitignore) 提供。"""
import os
from pathlib import Path


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        f = Path(__file__).resolve().parent / "data" / "sec_user_agent.txt"
        ua = f.read_text().strip() if f.exists() else ""
    if not ua:
        raise RuntimeError("請設定 SEC User-Agent: 環境變數 SEC_USER_AGENT, 或建立 data/sec_user_agent.txt, "
                           "內容如 'Your Name your@email.com' (SEC EDGAR 要求標明聯絡方式)")
    return ua
