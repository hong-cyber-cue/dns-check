# 手機端自動化設定指南

每台手機都要做這些設定，**約 15 分鐘 / 台**。第一台做完後，其他台可以照表抄。

## Part 1：基礎設定（必做）

### 1.1 開發者選項 → 保持喚醒（最重要）

1. 設定 → 關於手機 → **連點 7 次「版本號」**
2. 回到設定 → **開發者選項**
3. 開啟 **「保持喚醒狀態」**

效果：**只要插著充電線，螢幕永不熄屏**，徹底避免 Doze Mode 殺背景。

> 不同品牌位置：
> - 小米/紅米：設定 → 我的裝置 → 全部參數 → 連點 MIUI 版本
> - OPPO/Realme：設定 → 關於手機 → 版本 → 連點版本號
> - 三星：設定 → 關於手機 → 軟體資訊 → 連點 Build number

### 1.2 螢幕亮度調最低

設定 → 顯示 → 亮度 → 拉到最低（5% 左右）

OLED 螢幕全黑像素幾乎不耗電，省電費 + 不傷螢幕。

### 1.3 關閉鎖屏密碼（可選但推薦）

如果發生需要遠端讓人幫你戳一下螢幕的情況，沒密碼比較方便。

設定 → 安全性 → 螢幕鎖定 → **無**

當然這台手機就只能放在你信任的地方。

### 1.4 關閉自動更新

避免半夜系統自動更新重啟。

設定 → 系統 → 系統更新 → **關閉自動下載/安裝**

Play Store 那邊也關：Play Store → 設定 → 網路偏好 → 自動更新應用程式 → **不要自動更新**

### 1.5 鎖定螢幕方向

避免手機翻動觸發任何系統事件。

設定 → 顯示 → 自動旋轉 → **關閉**

## Part 2：App 安裝

從 Google Play 安裝這 3 個 App：

1. **Tailscale**（官方）
2. **Every Proxy**（搜尋作者 Stuart Boston）
3. **MacroDroid**（免費版夠用）

選擇性安裝：
4. **Termux**（如果你想跑額外腳本）

## Part 3：Tailscale 設定

1. 開啟 Tailscale，點 **Sign in**
2. 用團隊共用 Google 帳號登入
3. 開啟主開關
4. **記下分配到的 IP**（例如 `100.64.0.5`）寫進你的清單
5. **必要**：右上選單 → **Settings** → 確認以下開啟：
   - Use Tailscale DNS：**關閉**（重要！我們要用 ISP 預設 DNS 才能測污染）
   - Allow LAN access：保持預設

> ⚠️ **DNS 設定特別重要**
> 如果開啟 Tailscale DNS，會接管整個手機的 DNS，導致我們透過手機出去的 DNS 查詢全部走 Tailscale 而不是 ISP，這樣就測不到封鎖了。**必須關閉。**

## Part 4：Every Proxy 設定

1. 開啟 Every Proxy
2. 找到 **SOCKS5** 區塊 → 打開開關
3. 設定：
   - **Port**：`1080`
   - **Allow LAN connections**：✅ 打勾（很重要，不然 Tailscale 連不到）
   - **Bind to all interfaces**：✅ 打勾
4. 同樣記下這個 port

驗證：另一台手機或電腦同個 WiFi 試試 `nc -zv [手機IP] 1080`，應該回 succeeded。

## Part 5：MacroDroid 自動化設定

開啟 MacroDroid，按照以下建立 4 個 Macro：

### Macro 1：每日凌晨重啟

**目的**：每天 04:00 自動重啟手機，清除累積問題

1. 點「+」新增 Macro
2. **Trigger**（觸發）：Time/Date → Day of week / Time → 選每天 04:00
3. **Action**（動作）：Device Actions → Reboot
4. **儲存** 命名為 `Daily Reboot 04:00`

> 注意：Reboot 動作需要 ROOT 權限。如果沒 root，改用以下替代：
> - Action 改成「強制停止所有 App + 重啟 Tailscale」
> - 或者實體買一個「USB 智能定時插座」，每天定時斷電 2 分鐘

### Macro 2：自動重啟 Tailscale

**目的**：偵測 Tailscale 沒在跑就重開

1. 新增 Macro
2. **Trigger**：Periodic → 每 5 分鐘
3. **Constraint**（條件）：App → Tailscale → Not running
4. **Action**：Launch Application → Tailscale
5. 儲存命名為 `Restart Tailscale`

### Macro 3：自動重啟 Every Proxy

**目的**：每小時強制重啟一次代理 App，避免長時間運行記憶體洩漏

1. 新增 Macro
2. **Trigger**：Time/Date → 每小時的整點
3. **Action**：
   - Force stop app → Every Proxy
   - Wait 5 seconds
   - Launch Application → Every Proxy
4. 儲存命名為 `Hourly Restart Proxy`

### Macro 4：充電線拔除告警

**目的**：手機被拔電要立刻知道

1. 新增 Macro
2. **Trigger**：Power → External Power → Disconnected
3. **Action**：HTTP Request
   - URL：`https://api.telegram.org/bot<你的TOKEN>/sendMessage?chat_id=<你的CHAT_ID>&text=⚠️ 手機 [越南-Viettel] 充電線被拔除`
   - Method：GET
4. 儲存命名為 `Power Loss Alert`

把每台手機的 Macro 4 訊息改成對應的 ISP 名稱，這樣告警時你知道是哪台。

## Part 6：電池白名單（雙重保險）

即使開了「保持喚醒」，電池白名單還是要做，避免拔電時也能撐一段：

### 通用步驟（所有 Android）

設定 → 應用程式 → 找到以下 3 個 App，每個都做相同設定：

- Tailscale
- Every Proxy
- MacroDroid

設定項目：
1. **電池** → 不限制 / 不省電
2. **自啟動** → 允許
3. **背景活動** → 允許
4. **背景數據使用** → 允許

### 各品牌特殊設定

**小米 / 紅米**：
- 設定 → 應用設定 → 應用管理 → [App] → 省電策略 → **無限制**
- 設定 → 應用設定 → 應用管理 → [App] → 自啟動 → ✅
- 鎖屏清理 → 排除清單 → 加入這 3 個 App

**OPPO / Realme**：
- 設定 → 電池 → 應用耗電管理 → [App] → 允許後台運行 ✅
- 設定 → 應用管理 → 應用清單 → [App] → 應用權限 → 允許自啟動

**Vivo**：
- 設定 → 電池 → 後台耗電管理 → [App] → 允許後台高耗電

**三星**：
- 設定 → 應用 → [App] → 電池 → **不受限**
- 設定 → 裝置維護 → 電池 → 永不睡眠應用 → 加入

**華為**：
- 設定 → 電池 → 應用啟動管理 → [App] → 手動管理 → 全部開啟

## Part 7：驗證設定

設定完成後做這個壓力測試：

### 測試 A：螢幕熄屏不掉線
1. 不要插電，按電源鍵熄屏
2. 等 10 分鐘
3. 從 VPS 跑 `nc -zv 100.64.0.5 1080` → 應該還通

如果 10 分鐘後就連不上，代表 Doze Mode 沒擋住，回 Part 1 確認「保持喚醒」開了。

### 測試 B：插電 24 小時測試
1. 插電、開啟「保持喚醒」
2. 把手機放置 24 小時
3. 期間從 VPS 每小時 ping 一次手機
4. 看連續 24 個小時是否都通

如果某個小時開始斷，看 log 找原因（通常是 App 被殺，回 Part 6 加強白名單）。

### 測試 C：模擬重啟自動恢復
1. 手動重啟手機
2. 不要打開任何 App
3. 等 2 分鐘
4. 從 VPS 試連 SOCKS5 → 應該自動恢復

如果沒恢復，代表「自啟動」設定沒生效。

## Part 8：故障排查

### 常見問題清單

| 症狀 | 原因 | 解法 |
|------|------|------|
| 螢幕一關就斷 | Doze Mode | 開「保持喚醒」+ 充電 |
| 半夜自己斷 | 系統自動更新 | 關自動更新 |
| 重啟後不自動恢復 | 自啟動沒開 | Part 6 加強白名單 |
| Tailscale 顯示在線但連不上 | Every Proxy 掛了 | Macro 3 自動重啟 |
| 一段時間就斷一次 | 國產 ROM 激進 | Macro 2、3 自動拉起 |
| 充電中還是斷 | 充電器太弱供電不穩 | 換成 18W 以上充電器 |

### 如果某台手機問題特別多

建議的處理優先級：
1. 先試「重設手機」恢復出廠設定，重新跑流程
2. 還不行 → 換手機（建議買原生 Android 機或 Pixel）
3. 預算夠 → 直接買 iPhone（但要解決 iOS 跑 SOCKS5 的問題，比較複雜，不推薦）

## Part 9：完成檢核表

部署一台手機完成後，確認：

```
□ 開發者選項「保持喚醒」已開
□ 螢幕亮度最低
□ 自動更新已關
□ Tailscale 已登入，記下 IP
□ Tailscale 的「Use Tailscale DNS」已關閉
□ Every Proxy SOCKS5 已開（port 1080）
□ 3 個 App 都加入電池白名單
□ MacroDroid 4 個 Macro 已設定並啟用
□ 測試 A、B、C 全部通過
□ 手機資訊登記在主清單（IP / SIM / ISP / 位置）
```

## Part 10：手機資訊登記表（範本）

把這個表格放在共用文件，每台部署完填寫：

| # | ISP | 國家 | SIM 號碼 | Tailscale IP | 手機型號 | 序號 | 放置位置 | 負責人 | 部署日 |
|---|-----|------|---------|--------------|---------|------|---------|--------|--------|
| 1 | Viettel | VN | 0xxx-xxxx-xxx | 100.64.0.5 | Redmi 12 | xxx | 越南辦公室書架 | 阿明 | 2026-06-01 |
| 2 | Vinaphone | VN | 0xxx-xxxx-xxx | 100.64.0.6 | Redmi 12 | xxx | 越南辦公室書架 | 阿明 | 2026-06-01 |
| ... | | | | | | | | | |

未來手機掛了，照這張表能立刻找到對應手機。
