# DNS 封鎖檢測系統部署文件

## 架構

```
[6 台 Android 手機 + 不同電信 SIM]
    ↓ 每台跑 Tailscale + Every Proxy (SOCKS5)
[Tailscale 虛擬網路 100.64.x.x]
    ↑
[VPS：Python 檢測腳本 + SQLite + Grafana]
    ↓
[Telegram 告警 + 遠端面板]
```

---

## Part 1：手機端設置（每台手機都做一次）

### 1.1 基本準備

每台手機建議的條件：
- Android 8 以上
- 插好當地 SIM 卡，確認能用行動數據連網
- 關閉 WiFi（強制走 4G/5G，確保走 ISP 網路）
- 充電線常插
- 設定「螢幕永不熄屏」（開發者選項或螢幕設定）

### 1.2 安裝 Tailscale

1. Google Play 安裝 **Tailscale**
2. 開啟後選 **Sign in**，用同一個帳號（建議用團隊共用 Google 帳號）
3. 開啟 Tailscale 開關，記下分配到的 IP（例如 `100.64.0.5`）
4. 進入手機系統設定 → 應用程式 → Tailscale → **電池**，設為「不限制」/「不省電」

### 1.3 安裝 SOCKS5 代理

1. Google Play 安裝 **Every Proxy**
2. 進入 App → SOCKS5 → 開關打開
3. 預設 Port `1080`，**Bind to all interfaces** 要勾選
4. 同樣去系統設定把 Every Proxy 加入電池白名單

### 1.4 防止背景被殺（重要！）

依手機品牌設定：

**小米 / 紅米**：
- 設定 → 應用 → Tailscale → 自啟動 ✅
- 設定 → 應用 → Tailscale → 省電策略 → 無限制
- 對 Every Proxy 重複同樣設定

**OPPO / Realme**：
- 設定 → 電池 → 應用耗電管理 → Tailscale → 允許後台運行 ✅
- 同樣對 Every Proxy 處理

**Samsung**：
- 設定 → 應用 → Tailscale → 電池 → 不受限
- 設定 → 裝置維護 → 電池 → 永不睡眠的應用程式 → 加入兩個 App

### 1.5 設定每日自動重啟（強烈建議）

裝 **MacroDroid**（免費版夠用）：
- 觸發：每日 04:00
- 動作：重新開機

重啟後 Tailscale 跟 Every Proxy 會自動恢復（前提是電池白名單設定正確）。

### 1.6 紀錄手機資訊表

部署完每台後填寫：

| 編號 | ISP | SIM 電話 | Tailscale IP | 機型 | 放置位置 |
|------|-----|---------|--------------|------|---------|
| 1 | Viettel | 0xxx | 100.64.0.5 | Redmi 12 | 越南辦公室 |
| 2 | Vinaphone | 0xxx | 100.64.0.6 | Redmi 12 | 越南辦公室 |
| ... | | | | | |

---

## Part 2：VPS 端部署

### 2.1 VPS 規格與系統

- 推薦 DigitalOcean / Vultr / Hetzner
- 規格：2 vCPU / 4GB RAM / 50GB SSD
- 系統：Ubuntu 24.04 LTS
- 機房：**新加坡**（離越南菲律賓近，Tailscale 連線延遲低）

### 2.2 系統初始化

```bash
# 以 root 登入
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git sqlite3 nginx ufw curl

# 建立專案使用者
useradd -m -s /bin/bash dnscheck
mkdir -p /var/log/dnscheck
chown dnscheck:dnscheck /var/log/dnscheck
```

### 2.3 安裝 Tailscale

```bash
# 仍以 root 身份
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up

# 會印出一個 URL，瀏覽器打開登入同一個帳號
# 完成後 VPS 也加入 Tailscale 網路了
tailscale status   # 應該看到所有手機
```

測試是否能連到手機：
```bash
ping 100.64.0.5    # 應該通
nc -zv 100.64.0.5 1080   # 應該顯示 succeeded
```

### 2.4 部署檢測腳本

```bash
# 切到專案使用者
su - dnscheck

# Clone（或 scp 上傳）
git clone https://github.com/YOUR_REPO/dns-check.git
cd dns-check

# Python 環境
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 設定檔
cp config/config.yaml.example config/config.yaml
nano config/config.yaml   # 改成你的真實 IP / Token

# 域名清單
nano config/domains.txt
```

### 2.5 註冊 systemd 服務

```bash
# 切回 root
exit

# 複製 service 檔
cp /home/dnscheck/dns-check/systemd/dnscheck.service /etc/systemd/system/
cp /home/dnscheck/dns-check/systemd/dnscheck-health.service /etc/systemd/system/
cp /home/dnscheck/dns-check/systemd/dnscheck-health.timer /etc/systemd/system/

systemctl daemon-reload

# 主檢測服務
systemctl enable dnscheck
systemctl start dnscheck
systemctl status dnscheck

# 健康檢查定時器
systemctl enable dnscheck-health.timer
systemctl start dnscheck-health.timer

# 看即時 log
journalctl -u dnscheck -f
```

---

## Part 3：Grafana 面板

### 3.1 安裝 Grafana

```bash
apt-get install -y apt-transport-https software-properties-common
mkdir -p /etc/apt/keyrings/
wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor | tee /etc/apt/keyrings/grafana.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | tee /etc/apt/sources.list.d/grafana.list
apt-get update
apt-get install grafana -y

systemctl enable grafana-server
systemctl start grafana-server
```

### 3.2 安裝 SQLite plugin

```bash
grafana-cli plugins install frser-sqlite-datasource
systemctl restart grafana-server
```

### 3.3 設定 Grafana 讀取資料庫

讓 grafana 使用者能讀 SQLite 檔：
```bash
chmod 755 /home/dnscheck
chmod 755 /home/dnscheck/data
chmod 644 /home/dnscheck/data/results.db
```

進 Grafana 網頁（`http://your_vps_ip:3000`，預設 admin/admin）：

1. **Connections → Add data source → SQLite**
2. Path: `/home/dnscheck/data/results.db`
3. Save & test

### 3.4 建立矩陣面板

**Dashboard → New → Add visualization**

選 **State Timeline** 視覺化類型。

查詢：
```sql
SELECT 
  check_time as time,
  domain || ' @ ' || isp as metric,
  status
FROM results
WHERE $__unixEpochFilter(check_time)
ORDER BY check_time DESC
```

Field overrides：
- status = "ok" → 綠色
- status = "blocked" → 紅色  
- status = "unknown" / "suspect" → 黃色

### 3.5 建立統計面板

**封鎖率統計（最新 1 小時）：**
```sql
SELECT 
  isp,
  COUNT(*) as total,
  SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) as blocked,
  ROUND(SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as block_rate_pct
FROM results
WHERE check_time > strftime('%s', 'now') - 3600
GROUP BY isp
```

---

## Part 4：對外存取 Grafana（HTTPS）

### 4.1 設定 Nginx + Let's Encrypt

```bash
apt install certbot python3-certbot-nginx -y

cat > /etc/nginx/sites-available/grafana <<EOF
server {
    server_name dnsmonitor.yourdomain.com;
    
    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

ln -s /etc/nginx/sites-available/grafana /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx

# 申請 SSL
certbot --nginx -d dnsmonitor.yourdomain.com
```

### 4.2 防火牆

```bash
ufw allow 22
ufw allow 80
ufw allow 443
ufw enable
```

注意：Tailscale 流量不經過 ufw，所以不用特別開 100.64.0.0/10。

---

## Part 5：驗證部署

### 5.1 確認手機都通

```bash
# VPS 上
for ip in 100.64.0.5 100.64.0.6 100.64.0.7 100.64.0.8 100.64.0.9 100.64.0.10; do
    echo -n "$ip: "
    nc -zv -w 3 $ip 1080 2>&1 | grep -q succeeded && echo "OK" || echo "FAIL"
done
```

### 5.2 跑一次健康檢查

```bash
su - dnscheck
cd dns-check
source venv/bin/activate
python scripts/health_check.py
```

應該看到每個 ISP 的出口 IP / 國家 / Org 資訊，並且 Org 包含預期關鍵字。

### 5.3 跑一次手動檢測

```bash
# 同上環境下
python scripts/checker.py
# 看到結果就 Ctrl+C 停掉，systemd 會接手
```

---

## Part 6：日常維運

### 6.1 看 log

```bash
journalctl -u dnscheck -f                    # 主服務即時 log
journalctl -u dnscheck-health.service -n 50  # 最近健康檢查
tail -f /var/log/dnscheck/checker.log        # Python 寫的 log
```

### 6.2 改域名清單

```bash
su - dnscheck
nano dns-check/config/domains.txt
# 不用重啟，下一輪會自動讀取（如果寫了 reload 邏輯）
# 保險起見可以重啟：
sudo systemctl restart dnscheck
```

### 6.3 改 ISP / 新增手機

1. 手機端：裝好 Tailscale + Every Proxy，記下新 IP
2. VPS：編輯 `config/config.yaml`，加上新 ISP 區塊
3. `sudo systemctl restart dnscheck`

### 6.4 資料庫備份

```bash
# 加入 crontab
0 3 * * * sqlite3 /home/dnscheck/data/results.db ".backup /home/dnscheck/backup/results_$(date +\%Y\%m\%d).db"

# 保留 30 天
0 4 * * * find /home/dnscheck/backup -name "*.db" -mtime +30 -delete
```

### 6.5 常見問題排查

**某個 ISP 一直回 unknown / suspect：**
- 跑 `health_check.py` 看那個 SOCKS5 是否還活著
- 如果掛了，去看那台手機是不是 Every Proxy 被殺
- 重新 Tailscale 連一次

**所有 ISP 都 blocked：**
- 通常是腳本 bug 或 DoH 對照組查不到
- 看 log 找 `REAL_DNS_FAIL`

**Telegram 沒收到告警：**
- 確認 bot token 對、chat_id 對
- 第一次跑沒有告警是正常的（沒有歷史狀態可比對）

---

## Part 7：成本估算

**一次性：**
- 6 台 Android 手機（紅米 12 等）：約 NT$15000
- 6 張 SIM 卡（越南 3 + 菲律賓 3）：約 NT$1500

**每月：**
- VPS（2c4g）：USD 12（約 NT$380）
- 6 張 SIM 月租：約 NT$1500
- 域名 + Cloudflare：你已有
- Tailscale：免費版夠用
- **每月固定成本約 NT$2000**

---

## Part 8：下一步擴充

未來想加功能可以這樣擴：

1. **加截圖**：用 Playwright 跑無頭瀏覽器，封鎖時截圖
2. **加 SNI 檢測**：對 ASN 一致但連不上的域名，再測 TLS 握手
3. **加 HTTP 內容比對**：對比兩個 ISP 拿到的頁面 hash
4. **多區域 VPS**：把檢測腳本部署到不同國家 VPS 做交叉驗證
5. **API 開放**：讓內部其他系統查某個域名當前狀態
