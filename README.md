# DNS Block Checker（完整版 v2）

跨電信商 DNS 封鎖檢測系統。透過 Tailscale 連接分散在越南、菲律賓的手機，從各 ISP 真實網路檢測博弈域名是否被封鎖。

## 完整檔案結構

```
dns-check/
├── README.md                    ← 你在這裡
├── requirements.txt             ← Python 依賴
│
├── scripts/                     ← 程式碼
│   ├── checker.py              主檢測腳本（systemd 常駐）
│   ├── health_check.py         SOCKS5 出口健康檢查（每小時）
│   ├── telegram_notify.py      Telegram 告警模組
│   ├── status.py               CLI 查詢工具 ⭐ 新增
│   └── deploy.sh               VPS 一鍵部署
│
├── config/                     ← 設定檔
│   ├── config.yaml.example     設定檔範例（含 6 個 ISP）
│   └── domains.txt             要監測的域名清單
│
├── systemd/                    ← 系統服務
│   ├── dnscheck.service        主服務
│   ├── dnscheck-health.service 健康檢查
│   └── dnscheck-health.timer   每小時 timer
│
├── grafana/                    ← Grafana 面板 ⭐ 新增
│   ├── dashboard.json          一鍵匯入的儀表板
│   └── IMPORT.md               匯入步驟（3 分鐘）
│
└── docs/                       ← 文件
    ├── DEPLOY.md               完整部署指南（必讀）
    ├── MOBILE_SETUP.md         手機端設定 ⭐ 新增
    ├── TELEGRAM_SETUP.md       Telegram Bot 教學 ⭐ 新增
    └── TROUBLESHOOTING.md      故障排查 ⭐ 新增
```

## 部署流程總覽

按這個順序做，從零到完整運行：

### 階段 1：準備（採購 + 申請）
1. **採購硬體**：6 台 Android 手機 + 6 張 SIM 卡（越南 3 / 菲律賓 3）
2. **租 VPS**：DigitalOcean / Vultr 新加坡機房，2c4g
3. **申請帳號**：
   - Tailscale 帳號（用團隊 Google 共用帳號）
   - Telegram Bot（看 docs/TELEGRAM_SETUP.md）
4. **準備域名**：用來掛 Grafana 網頁（可選）

### 階段 2：手機端（每台 15 分鐘）
照著 **docs/MOBILE_SETUP.md** 做：
1. 開「保持喚醒」+ 螢幕亮度最低
2. 裝 Tailscale + Every Proxy + MacroDroid
3. 電池白名單 + MacroDroid 自動化規則
4. 跑 24 小時穩定性測試

重點：先做 1 台跑 48 小時驗證穩定，再批次複製到其他 5 台。

### 階段 3：VPS 端（一次 30 分鐘）
照著 **docs/DEPLOY.md** 做：
1. 系統初始化
2. 安裝 Tailscale
3. 部署檢測腳本
4. 編輯 config/config.yaml（填手機 IP / Telegram Token）
5. 編輯 config/domains.txt（填要監測的域名）
6. 啟動 systemd 服務

### 階段 4：查看結果（依場景選）
- **CLI 快速看**：./scripts/status.py（部署當天就能用）
- **Grafana 面板**：照 grafana/IMPORT.md 匯入 dashboard.json（3 分鐘）
- **Telegram 告警**：按 docs/TELEGRAM_SETUP.md 設定（5 分鐘）

### 階段 5：日常維運
看 docs/TROUBLESHOOTING.md 的「預防勝於治療」清單。

## 檢測邏輯

每個域名 × 每個 ISP 做兩步：

1. **透過手機 SOCKS5 出去**，問 ISP 預設 DNS（玩家實際會用的）
2. **從 VPS 透過 Cloudflare DoH** 問真實 IP（對照組）
3. **比對 ASN**：同 ASN → 正常，不同 ASN → DNS 污染封鎖

結果寫進 SQLite，三種方式查看：
- CLI（status.py）
- Grafana 矩陣熱力圖
- Telegram 狀態變化通知

## 三種查看方式對比

| 方式 | 用途 | 設定時間 | 適合場景 |
|------|------|---------|---------|
| status.py CLI | 快速查當前狀態 | 0 分鐘 | 部署當天 / debug |
| Grafana 面板 | 視覺化監控 | 3 分鐘匯入 | 日常監控主畫面 |
| Telegram 告警 | 被動接收變化 | 5 分鐘設定 | 隨身關注 |

**建議三個都用**：
- 平時主看 Grafana
- 重要變化靠 Telegram 提醒
- 出問題用 status.py + log 排查

## 預估成本

**一次性**：
- 6 台手機（紅米 12 等）：約 RM 2000-3000
- 6 張 SIM 卡：約 RM 200

**每月**：
- VPS：USD 12（約 RM 50）
- 6 張 SIM 月租：約 RM 150
- **每月約 RM 200**

## 快速啟動指令

```bash
# VPS 端首次部署
sudo bash scripts/deploy.sh

# CLI 查狀態
./scripts/status.py

# 重啟服務
sudo systemctl restart dnscheck

# 看 log
journalctl -u dnscheck -f
```

## 我建議的閱讀順序

**第一次看**：
1. 這份 README（你正在看）
2. docs/DEPLOY.md 了解整體部署
3. docs/MOBILE_SETUP.md 開始動手做第一台手機

**實作時參考**：
4. docs/TELEGRAM_SETUP.md 設定告警
5. grafana/IMPORT.md 匯入面板

**出問題時**：
6. docs/TROUBLESHOOTING.md 故障排查
