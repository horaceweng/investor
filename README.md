# 選股儀表板

本機網頁程式：在頁首選**股票池**（自訂觀察清單 / S&P 500 / Nasdaq 100），各分頁顯示該股票池的結果。
資料更新在背景執行，頁面上有更新按鈕。僅供研究，不構成投資建議。

| 分頁 | 內容 |
|---|---|
| 13週跌幅 / 本益比最低 / 淨值比最低 / 殖利率最高 | 依股票池篩選（自訂觀察清單不適用） |
| 神奇公式 | Greenblatt《The Little Book That Beats the Market》 |
| Navellier 評級 | 30% 基本面 + 70% 量化(Alpha/SD)；自訂觀察清單只作用於此分頁 |
| 大師買進 | 約 76 位價值型基金經理人的 SEC 13F（季度自動偵測，與股票池無關） |

## 安裝與執行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p data && echo "Your Name you@example.com" > data/sec_user_agent.txt   # SEC EDGAR 要求標明聯絡方式
.venv/bin/python main.py        # 開啟 http://127.0.0.1:8765
```

SEC User-Agent 也可用環境變數 `SEC_USER_AGENT` 提供。`data/`、`data_navellier/`（含自訂觀察清單）與各種快取不納入版本控制。

## 資料來源與注意事項

- Wikipedia（S&P 500 成分股）、Nasdaq 官方網站 API（Nasdaq 100 成分股，非正式文件化的 API）、Yahoo Finance via yfinance（價格/基本面）、SEC EDGAR 13F、OpenFIGI。
- Yahoo 會限流：失敗時保留舊資料，基本面/財報有續傳快取（約 3 天）。
- 啟動 `main.py` 時若看到 `Address already in use`，表示舊行程還占著埠，可用下列指令關掉：`lsof -tiTCP:8765 -sTCP:LISTEN | xargs kill`。
