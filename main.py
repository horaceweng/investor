#!/usr/bin/env python3
"""選股儀表板入口: 啟動本機網頁伺服器 (實作在 investor/web/server.py)。

用法:
  .venv/bin/python main.py                       # 啟動並開啟瀏覽器 http://127.0.0.1:8765
  .venv/bin/python main.py --port 9000 --no-open
也可: .venv/bin/python -m investor

launchd 常駐服務與文件都指向這個檔案, 所以保留在專案根目錄。
"""
from investor.web.server import main

if __name__ == "__main__":
    main()
