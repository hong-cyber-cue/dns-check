# 故障排查指南

部署完成後遇到問題的處理流程。從最常見的問題排到最罕見的。

## 快速診斷指令

不知道哪裡出問題時先跑這 3 個指令：

```bash
# 1. 主服務是否在跑
systemctl status dnscheck

# 2. 最近 50 行 log
journalctl -u dnscheck -n 50 --no-pager

# 3. 手動跑一次健康檢查
sudo -u dnscheck /home/dnscheck/dns-check/venv/bin/python \
  /home/dnscheck/dns-check/scripts/health_check.py
```

90% 的問題從這 3 個指令就能定位。

---

## 問題 1：某個 ISP 一直顯示 unknown / suspect

### 症狀
Grafana 或 status.py 看到某個 ISP 的某些域名一直是 `unknown` 或 `suspect` 狀態。

### 診斷步驟

**Step 1：確認手機代理還活著**
```bash
# 假設 Viettel 手機是 100.64.0.5
nc -zv -w 3 100.64.0.5 1080
```
- 顯示 `succeeded` → 連線正常，往 Step 2
- 顯示 `Connection refused` 或超時 → 跳到「問題 2」

**Step 2：確認透過 SOCKS5 真的能上網**
```bash
curl --socks5 100.64.0.5:1080 https://ipinfo.io/json
```
應該回傳 JSON 含當地 IP / ISP 資訊。

- 沒回應 → 手機行動數據可能斷了（SIM 卡停了 / 收訊問題）
- 回應的 `org` 不是預期 ISP → 手機可能切到 WiFi 了（必須關 WiFi 強制走 4G）

**Step 3：手動測 DNS 查詢**
```bash
# 進 Python 環境測
sudo -u dnscheck bash
cd /home/dnscheck/dns-check
source venv/bin/activate
python3 << 'EOF'
import asyncio
from scripts.checker import query_isp_dns_via_socks5

async def test():
    result = await query_isp_dns_via_socks5(
        "pbv88.com",
        "203.113.131.1",  # Viettel DNS
        "100.64.0.5",     # 手機 Tailscale IP
        1080
    )
    print("結果:", result)

asyncio.run(test())
EOF
```

- 拿到 IP → DNS 查詢沒問題，是後續邏輯有 bug
- 拿不到 IP → ISP DNS 伺服器可能變了，查最新的 ISP DNS IP 更新 config.yaml

### 常見原因排序

1. **手機 SIM 卡欠費停用**（占約 40% 案例）→ 儲值
2. **手機切到 WiFi 走錯網路**（占約 30%）→ 關 WiFi
3. **Every Proxy App 被殺**（占約 20%）→ 加電池白名單 + MacroDroid 自動拉起
4. **ISP DNS IP 變了**（占約 5%）→ 更新 config.yaml
5. **Tailscale 連線異常**（占約 5%）→ 手機端重啟 Tailscale

---

## 問題 2：手機 SOCKS5 連不上（nc 顯示 refused）

### 症狀
從 VPS `nc -zv 100.64.0.5 1080` 顯示 `Connection refused` 或超時。

### 診斷流程圖

```
nc 連不上手機 SOCKS5
      ↓
Step 1: 能 ping 通手機嗎？
  ping 100.64.0.5
      ↓
   ┌──┴──┐
  通     不通
   ↓      ↓
 Step 2  Tailscale 沒連上
         → 看 Tailscale App 是否在執行
         → 重啟 Tailscale
         → 確認 Wi-Fi/數據開啟
      
Step 2: Every Proxy 是否在跑？
  Tailscale 通，但 SOCKS5 不通
  → 手機端打開 Every Proxy
  → 確認 SOCKS5 開關開啟
  → port 1080，Bind all interfaces 勾選
  → 重啟 Every Proxy
```

### Tailscale 通但 SOCKS5 不通

最常見原因：**Every Proxy 沒勾「Allow LAN connections」**

每次 Every Proxy 重啟後可能會重置這個選項，要進 App 確認：
- SOCKS5 → Settings → Allow LAN connections ✅

### 完全找不到手機

如果 `tailscale status` 看不到那台手機，可能是：
- 手機端 Tailscale 被踢出（Auth Key 過期）→ 重新登入
- 手機沒網路 → 確認 SIM 收訊或開個熱點測一下

---

## 問題 3：所有 ISP 都顯示 blocked

### 症狀
所有域名在所有 ISP 都顯示 blocked，明顯不正常。

### 可能原因

**原因 A：對照組 DoH 查不到（最常見）**

DoH（透過 VPS 直接查 Cloudflare 1.1.1.1）如果失敗，所有比對都會異常。

測試：
```bash
curl 'https://1.1.1.1/dns-query?name=pbv88.com&type=A' \
  -H 'Accept: application/dns-json'
```

- 沒回應 → VPS 的 Cloudflare 路由有問題或被當地網路封了
- 正常 → 看 log 細節

**原因 B：腳本 bug**
看 log 找 traceback：
```bash
journalctl -u dnscheck -n 200 | grep -A 20 "Traceback"
```

**原因 C：資料庫權限問題**
```bash
ls -la /home/dnscheck/data/results.db
# 應該是 dnscheck:dnscheck 擁有，可讀可寫
```

---

## 問題 4：Telegram 沒收到告警

### 診斷順序

**1. 確認設定檔有填**
```bash
grep -E "telegram_(enabled|bot_token|chat_id)" \
  /home/dnscheck/dns-check/config/config.yaml
```

**2. 確認 token 有效**
```bash
curl "https://api.telegram.org/bot<YOUR_TOKEN>/getMe"
```
應該回 bot 資訊 JSON。回 `Unauthorized` = token 錯。

**3. 手動發訊息測試**
```bash
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/sendMessage" \
  -d "chat_id=<YOUR_CHAT_ID>" \
  -d "text=測試"
```
- 群組收到 → 設定 OK，是腳本邏輯問題
- `chat not found` → chat_id 錯了
- `Forbidden: bot is not a member` → bot 沒在群組或被踢

**4. 看腳本 log 是否真的有觸發**
```bash
journalctl -u dnscheck | grep -i "telegram\|alert\|狀態變化"
```

**5. 常見誤解：第一輪沒告警是正常**

腳本的告警邏輯是「狀態**變化**才發」。系統剛啟動時所有域名都是「第一次記錄」，沒有歷史可比對，所以不發告警。**第二輪檢測**之後狀態有變才會推。

如果想測試告警是否能正常運作：
- 手動改某個域名的 DNS 記錄
- 或者編輯 SQLite 把某筆紀錄的 status 改掉，再等下輪檢測

---

## 問題 5：Grafana 面板顯示 No data

### 排查步驟

**1. 資料庫有資料嗎？**
```bash
sqlite3 /home/dnscheck/data/results.db "SELECT COUNT(*) FROM results;"
```
- 回 0 → 檢測腳本沒跑過或沒寫入，看主服務 log
- 有數字 → 往下

**2. Grafana 能讀資料庫嗎？**

進 Grafana → Connections → Data sources → 點你建的 SQLite → **Test**
- 綠勾 → 連線 OK
- 紅叉 → 看錯誤訊息，通常是檔案路徑錯或權限不對

**權限修正**：
```bash
# Grafana 預設用 grafana 使用者跑
chmod 755 /home/dnscheck
chmod 755 /home/dnscheck/data
chmod 644 /home/dnscheck/data/results.db
```

**3. SQL 查詢有沒有對到表結構？**

Dashboard JSON 寫的 ISP 名稱要跟你 config.yaml 一致。例如 dashboard 寫 `Viettel`，但你 config 寫成 `viettel`（小寫），就會 match 不到。

修法 1：改 config 用 dashboard 預設名稱
修法 2：編輯 dashboard SQL，改成你的 ISP 名稱

---

## 問題 6：檢測結果跟玩家實際體驗不符

### 症狀
系統說某域名 OK，但玩家回報打不開（或反之）。

### 可能原因

**原因 A：手機走錯網路**
玩家用的是手機 4G/5G，但你的監測手機切到 WiFi 了。WiFi 走的是辦公室寬頻，不是電信網路。

檢查：
- 手機端關閉 WiFi
- 透過 `curl --socks5` 確認出口 IP 是當地電信 IP

**原因 B：DNS 沒污染但 SNI 被封**
你的系統只測 DNS 層，遇到「ASN 一致但 TLS 握手被擋」這種情況會誤判為 OK。

這在越南是真實存在的（雖然少數），需要補測 SNI。如果遇到很多次，可以擴充腳本加 TLS 探測。

**原因 C：玩家用的是不同 DNS**
有些玩家自己改了 DNS（例如用 1.1.1.1 或公司 DNS），他們不會被 ISP DNS 污染影響，跟你監測結果不一致是正常的。

**原因 D：CDN 路由問題**
Cloudflare 在某些電信網路的路由是新加坡，某些是越南本地。如果某個 PoP 出問題，玩家連不上但 DNS 查詢沒問題。

這個比較難測，需要做 HTTP 層 RTT 監控。

### 處理建議
- 90% 的情況用 DNS 檢測就夠
- 遇到「玩家說打不開但監測 OK」累積 3 次以上 → 考慮加 SNI 檢測
- 不要追求 100% 準確，**抓主要矛盾**

---

## 問題 7：資料庫越來越大

### 症狀
跑了幾個月後發現 results.db 變成幾百 MB。

### 估算
- 每筆紀錄約 200 bytes
- 100 域名 × 6 ISP × 一天 48 次（每 30 分鐘） = 28800 筆 / 天
- 一天約 5.7 MB
- 一年約 2 GB

不算很大，但可以做清理：

**清理腳本**（加進 crontab，每週跑一次）：
```bash
sqlite3 /home/dnscheck/data/results.db <<EOF
-- 刪除 90 天前的細節資料，只保留每天的彙總
DELETE FROM results WHERE check_time < strftime('%s', 'now', '-90 days');
VACUUM;
EOF
```

**長期歸檔策略**：
- 最近 30 天：原始資料（每 30 分鐘一筆）
- 30-90 天：彙總成每小時一筆
- 90 天以上：彙總成每天一筆 + 變化事件清單

需要的話我可以幫你寫歸檔腳本。

---

## 問題 8：某天突然所有 ISP 都掛了

### 可能原因（按機率排序）

1. **VPS 被攻擊或斷網** → 看 VPS 狀態，重啟服務
2. **Tailscale 服務當機**（極罕見但發生過）→ 重啟 Tailscaled
3. **DNS over TCP 被 ISP 全面封鎖** → 改用 UDP 53 或 DoH
4. **公共 Wi-Fi 中斷導致手機批量離線** → 確認手機都在用 4G
5. **辦公室斷電**（如果手機在同一地點）→ 加 UPS

### 重啟順序

VPS 端：
```bash
systemctl restart tailscaled
systemctl restart dnscheck
journalctl -u dnscheck -f  # 看是否恢復
```

手機端（用 Tailscale Admin Console 一台一台檢查）：
- https://login.tailscale.com/admin/machines
- 看每台手機最後在線時間，掉線的要實體去處理

---

## 問題 9：腳本佔用 CPU 太高

### 診斷
```bash
top -p $(pgrep -f checker.py)
```

正常情況：< 5% CPU

過高可能原因：
- 域名清單太長（500+ 條）
- 檢測間隔太短（< 5 分鐘）
- 沒設定 `between_check_ms`（DNS 查詢太密集）

### 調整
編輯 `config.yaml`：
```yaml
check_interval_minutes: 60  # 從 30 改成 60
between_check_ms: 500       # 從 200 改成 500
```

重啟服務：
```bash
sudo systemctl restart dnscheck
```

---

## 問題 10：完全不知道哪裡有問題

如果以上都不符合你的症狀，按這個順序蒐集資訊：

```bash
# 1. 系統狀態
systemctl status dnscheck
systemctl status dnscheck-health.timer

# 2. 最近 200 行 log
journalctl -u dnscheck -n 200 --no-pager > /tmp/dnscheck.log
journalctl -u dnscheck-health.service -n 50 --no-pager > /tmp/health.log

# 3. Tailscale 狀態
tailscale status

# 4. 資料庫狀態
sqlite3 /home/dnscheck/data/results.db <<EOF
SELECT COUNT(*) as total, MIN(datetime(check_time, 'unixepoch')) as earliest,
       MAX(datetime(check_time, 'unixepoch')) as latest FROM results;
SELECT status, COUNT(*) FROM results 
  WHERE check_time > strftime('%s', 'now', '-1 hour') 
  GROUP BY status;
EOF

# 5. 設定檔（記得移除 token 再分享）
cat /home/dnscheck/dns-check/config/config.yaml | grep -v token
```

把這些資訊整理後再來分析，通常根本原因會浮現。

---

## 預防勝於治療

每週固定做這些事：

| 頻率 | 任務 | 指令 |
|------|------|------|
| 每天 | 看 Telegram 告警是否有異常 | （手機看） |
| 每週 | 跑 health_check 確認 6 個代理都在 | `python scripts/health_check.py` |
| 每週 | 確認 SIM 卡餘額充足 | 進電信商 App |
| 每月 | 看 Grafana 趨勢，找慢性問題 | （瀏覽器） |
| 每月 | 備份資料庫 | `cp results.db backup/` |
| 每季 | 更新 Tailscale 跟系統套件 | `apt update && apt upgrade` |

這份清單貼在內部 Wiki，輪班看護的話交接就靠這張。
