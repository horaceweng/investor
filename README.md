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
mkdir -p data/config && echo "Your Name you@example.com" > data/config/sec_user_agent.txt   # SEC EDGAR 要求標明聯絡方式
.venv/bin/python main.py        # 開啟 http://127.0.0.1:8765   (也可: python -m investor)
```

SEC User-Agent 也可用環境變數 `SEC_USER_AGENT` 提供。

## 專案結構

```
main.py                    入口 (只有幾行; launchd 常駐服務指向它)
investor/                  套件
├── paths.py               所有路徑集中一處 (含舊版資料自動搬遷)
├── fileio.py              原子寫入、JSON 讀寫
├── universe.py            成分股: S&P 500 / Nasdaq 100 (全專案唯一來源)
├── data_sources/          外部資料存取: yahoo.py(限流重試) / sec.py(EDGAR) / openfigi.py / prices.py(1M-1Y 表現)
├── screens/               選股: losers(13週跌幅) / value(本益比、淨值比、殖利率) / magic(神奇公式)
├── superinvestors/        大師 13F: buys.py + managers.csv (經理人名單)
├── navellier/             Navellier 評級
│   ├── factors.py         基本面因子公式 (純計算)       ├── alpha_beta.py   Alpha/SD、Beta (52 週)
│   ├── grading.py         評級: 五分位、30/70 綜合 (純計算) ├── history.py      每週分數歷史
│   ├── fundamentals.py    抓取 + 快取 (原始季度序列)     ├── settings.py     觀察清單 / 股票池 / 動能門檻
│   └── rating.py          整合: run() 與命令列
└── web/                   網頁
    ├── server.py          HTTP 伺服器 (驗證閘門、路由)    ├── access.py   存取政策 (Host/Tailscale 帳號與裝置)
    ├── api.py             JSON 操作 (一個函式一個動作)    ├── auth.py     密碼登入 (選用)
    ├── jobs.py / steps.py 背景工作與更新步驟              ├── store.py    網頁狀態 (data/state.pkl)
    ├── presenters.py      評級結果 -> 表格 (純計算)
    └── render/            頁面: page.py / tabs.py (一個分頁一個函式) / cells.py / static/app.css, app.js
tools/resolve_managers.py  維護工具: 解析經理人 CIK (不會覆蓋 managers.csv)
tests/                     單元測試
```

**依賴方向 (由下往上, 不可反向)：** `paths, fileio, universe` → `data_sources` → `screens / superinvestors / navellier`（彼此獨立）→ `web`。
選股與評級模組不知道網頁的存在；網頁層只負責把它們組合起來。

各選股程式也能單獨在命令列執行：

```bash
.venv/bin/python -m investor.screens.losers --universe ndx
.venv/bin/python -m investor.screens.value --cached
.venv/bin/python -m investor.navellier.rating sp500
.venv/bin/python -m investor.superinvestors.buys
```

## 資料目錄 (`data/`，不納入版本控制)

```
data/config/     本機設定與機密: SEC User-Agent、遠端存取名單、密碼、session 金鑰
data/user/       使用者設定: 自訂觀察清單、股票池選擇、動能門檻 (值得備份)
data/navellier/  Navellier 每週歷史與報告
data/cache/      可重新產生的快取: SEC 申報、基本面(原始季度序列)、成分股、選股中間檔
data/exports/    各選股程式單獨執行時匯出的 CSV
data/state.pkl   網頁目前顯示的各項結果
```

舊版（平鋪結構）的資料檔會在啟動時自動搬到上述位置，已搬過或新位置已有同名檔則略過，不會覆蓋。

## 測試

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -t .      # 約 90 個測試, 不連網、不碰 data/
.venv/bin/python -m pyflakes investor tests tools main.py
```

測試固定了幾個爭議過的定義 (如盈餘動能 = 成長率的變化率)、存取控制的矩陣 (含 `X-Forwarded-For` 偽造的回歸測試)，
以及路徑與資料搬遷。網路相關的部分 (抓資料) 沒有單元測試。

## 資料來源與注意事項

- Wikipedia（S&P 500 成分股）、Nasdaq 官方網站 API（Nasdaq 100 成分股，非正式文件化的 API）、Yahoo Finance via yfinance（價格/基本面）、TradingView 公開 screener（補充基本面與分析師預估）、SEC EDGAR 13F、OpenFIGI。
- Yahoo 會限流：失敗時保留舊資料，基本面/財報有續傳快取（約 3 天）。
- TradingView 用來補足 Yahoo 最新一季的空欄位（營收、淨利、EPS、自由現金流），並提供當下共識分析師預估（無歷史）。

**預估修正（Analyst Earnings Estimate Revision）因子**：
- 每次執行程式時會存一份預估快照 (`data/navellier/estimates.jsonl`)，累積後用來比較前後快照算預估修正%。
- 修正 = (最新預估 - 約 28 天前預估) / |舊預估| × 100%；需最少 7 天間隔且同一財報期才有值。
- 因此新執行後至少需累積 7-28 天才會開始出現修正數值，早期快照不足時該因子為空（不影響評級，MIN_FACTORS 維持 5）。

**技術面指標**（日線，僅供顯示參考，不進評級與篩選）：
- RSI（相對強弱指數）、趨勢（多頭/回檔/空頭）、距 52 週高（%）、MACD（轉強/轉弱）、技術評等。
- **自訂清單分類** (`navellier/groups.py`, `data/user/groups.json`): 只決定表格怎麼分段顯示並附各類摘要；頁面「編輯分類」每行填「分類名: 代號 代號 …」，新加入的股票會有「尚未分類」提示可逐檔選類。清單增減或分類改了 (指紋不同) 會自動重算。
- **自訂清單的評級是對「同產業全市場」比** (`navellier/peers.py`)：清單裡的股票本來就是篩選過的，清單內互比沒有意義，所以每檔改跟 TradingView 同產業、市值 ≥ 20 億美元的全部美股 (普通股與 ADR，不含優先股) 比百分位。目標與同業的基本面因子都用同一份 TradingView 資料、同一套算法；量化分數 (52 週 Alpha/SD) 用同一函式與基準，同業分數依「已收完的週」存在 `data/cache/peer_scores.json`，同一週內不重抓、分批存檔可接續。限制：TradingView 沒有營業利益與股東權益的逐季歷史，所以同業比較沒有「營業利益率年增」，財報驚喜只有最新一季，預估修正不計；ETF 沒有同業基準，不評等級；同業少於 10 檔也不評。S&P 500 / Nasdaq 100 股票池不受影響。
- **全市場動能榜** (`screens/momentum.py`, 分頁「全市場動能榜」): 掃描 NYSE/NASDAQ/AMEX 市值 ≥ 20 億美元的普通股與 ADR (約 2,000 檔), 依 TradingView 產業分段, 列出 13 / 26 / 52 週 Alpha/SD 的全市場百分位、贏 SPY 週數、40 週線、離 52 週高、波動, 並標記「持續強勢」「年與半年都強」「產業順風」「高波動」。條件來自 2019–2026 全市場週資料回測 (point-in-time; 結論與限制寫在分頁說明裡)；只是觀察清單的來源, 不是買進訊號。週收盤依「已收完的週」快取在 `data/cache/market_weekly.pkl`, 每週自動更新一次; 每週候選存進 `data/navellier/momentum_history.jsonl` 供日後驗證。
- **自動更新**: 啟動程式與每次載入頁面時 (`web/autorefresh.py`) 檢查資料是否過期，過期才在背景更新，已是最新就不上網。技術面每個交易日一次（週末沿用週五；只抓一次 TradingView，不重跑評級）；評級每週一次（最近已收完的週五變了才重算）。失敗後退避重試（技術面 1 小時、評級 6 小時），舊資料保留。按鈕仍可手動強制更新。
- 同一快照中存儲技術面數據供網頁表格顯示。舊快照格式（無 tech 欄位）與新格式自動相容；技術面抓取失敗時只警告，不中斷評級。
- **財報欄位屬基本面**，放在「基本面因子明細」表：下次財報日（含倒數天數）、最新財報日、最新季 EPS／營收驚喜%、結果（雙 beat／雙 miss…；驚喜% 大於 0 即 beat）。與技術面一樣只顯示、不進評分，每次快照存檔供日後驗證。
- 提示邏輯：評級 A + RSI 過熱 → 等回檔；評級 A/B + 趨勢回檔 → 可留意；被建議移除 + 趨勢空頭 → 弱勢確認；財報 ≤7 天 → 財報在 N 天內；財報公布 30 天內 EPS beat 但營收 miss → 提醒、雙 miss → 提醒。

- 啟動 `main.py` 時若看到 `Address already in use`，表示舊行程還占著埠，可用下列指令關掉：`lsof -tiTCP:8765 -sTCP:LISTEN | xargs kill`。

## 常駐與遠端存取 (macOS + Tailscale)

**常駐 (launchd)**：`~/Library/LaunchAgents/com.horaceweng.investor.plist` 以 `.venv/bin/python main.py --no-open` 啟動, `KeepAlive` 當掉會自動重啟。

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.horaceweng.investor.plist   # 啟動 / 登入時自動啟動
launchctl kickstart -k gui/$(id -u)/com.horaceweng.investor                              # 改了程式後重啟
launchctl bootout gui/$(id -u)/com.horaceweng.investor                                   # 停止並取消常駐
tail -f data/server.log
```

**遠端存取：只限指定的 Tailscale 裝置。** 程式永遠只綁定 `127.0.0.1`; 外部連線走 `tailscale serve` (僅 tailnet 內, **不是** Funnel):

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8765   # https://<本機>.<tailnet>.ts.net:8443  (一定要用 https:// 與完整網域名, 不能用 IP)
tailscale serve --https=8443 off                          # 關閉
```

程式端另有檢查 (設定檔在 `data/config/`, 見 `web/access.py`、`web/auth.py`)：
- `allowed_hosts.txt`: 允許的 Host 名稱 (tailnet 主機名)。
- `tailscale_users.txt`: 只放行這些 Tailscale 帳號 (`Tailscale-User-Login` 標頭; 公開的 Funnel 請求沒有)。
- `tailscale_devices.txt`: 只放行這些裝置 (一行一個, 如 `iphone-14-pro-max`); 由 `X-Forwarded-For` **最後一段** 呼叫 `tailscale whois` 反查, 不在名單內一律 403。
- 可另設 `dashboard_password.txt` 啟用密碼登入。綁定非本機位址時一定要有密碼, 否則拒絕啟動。

在 Mac mini 本機請直接用 `http://127.0.0.1:8765` (本機不需標頭); 經 tailnet 網址從本機連會被擋 (本機不在裝置名單)。
