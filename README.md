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
tailscale serve --bg --https=8443 http://127.0.0.1:8765   # https://<本機>.<tailnet>.ts.net:8443
tailscale serve --https=8443 off                          # 關閉
```

程式端另有兩層檢查 (設定檔都在 `data/`, 不納入版本控制, 見 `auth.py`):
- `allowed_hosts.txt`: 允許的 Host 名稱 (tailnet 主機名)。
- `tailscale_users.txt`: 只放行這些 Tailscale 帳號 (`Tailscale-User-Login` 標頭; 公開的 Funnel 請求沒有)。
- `tailscale_devices.txt`: 只放行這些裝置 (一行一個, 如 `iphone-14-pro-max`); 由 `X-Forwarded-For` **最後一段** 呼叫 `tailscale whois` 反查, 不在名單內一律 403。
- 可另設 `dashboard_password.txt` 啟用密碼登入。綁定非本機位址時一定要有密碼, 否則拒絕啟動。

測試時在 Mac mini 本機請直接用 `http://127.0.0.1:8765` (本機不需標頭); 經 tailnet 網址從本機連會被擋 (本機不在裝置名單)。
