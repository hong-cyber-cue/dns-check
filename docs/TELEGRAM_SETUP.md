# Telegram Bot 設定教學

整個流程約 5 分鐘。完成後系統會在域名狀態變化時自動推播給你。

## Step 1：建立 Bot

1. 用 Telegram 找 **@BotFather**（官方驗證帳號，藍勾勾）
2. 對它輸入 `/newbot`
3. 它會問：
   - **Bot 名稱**（顯示用）：例如 `DNS Monitor Bot`
   - **Bot 用戶名**（必須以 bot 結尾）：例如 `pb_dns_monitor_bot`
4. 成功後 BotFather 會給你一個 **Token**，長這樣：
   ```
   7654321098:AAH-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   **這個 token 保管好，別外洩**（外洩等於任何人能用你的 bot 發垃圾訊息）

## Step 2：建立群組（推薦）

直接讓 bot 私訊你也可以，但推薦建群組，方便團隊一起看：

1. Telegram 點「新建群組」
2. 群組名稱：例如 `DNS監控告警`
3. 加入成員：
   - 你自己
   - 你的 bot（搜尋它的用戶名 `@pb_dns_monitor_bot`）
   - 其他想接收告警的同事

### 重要：把 bot 設為管理員

群組建好後：
1. 點群組名稱 → 編輯 → 管理員
2. 把 bot 加入管理員（給「發送訊息」權限就夠）

不設管理員的話，某些群組設定下 bot 發不出訊息。

## Step 3：拿 Chat ID

這步比較容易卡，照做就好：

### 方法 A：用 @userinfobot（最簡單）

1. 把 **@userinfobot** 加入你的群組
2. 它會自動顯示這個群組的 ID（負數，例如 `-1001234567890`）
3. 記下這個 ID，然後可以把 userinfobot 踢出群組

### 方法 B：用 API 自己查

1. 在群組裡隨便發一則訊息（讓 bot 看到群組存在）
2. 瀏覽器打開：
   ```
   https://api.telegram.org/bot<你的TOKEN>/getUpdates
   ```
   把 `<你的TOKEN>` 換成 Step 1 拿到的 token
3. 看 JSON 裡的 `chat.id`，那就是 chat_id

範例回應：
```json
{
  "ok": true,
  "result": [{
    "message": {
      "chat": {
        "id": -1001234567890,    ← 這個就是 chat_id
        "title": "DNS監控告警",
        "type": "supergroup"
      }
    }
  }]
}
```

**注意**：群組的 chat_id 是**負數**，私訊的 chat_id 是**正數**，兩個都對，視你的場景。

## Step 4：測試訊息

確認設定可用：

```bash
curl -X POST "https://api.telegram.org/bot<你的TOKEN>/sendMessage" \
  -d "chat_id=<你的CHAT_ID>" \
  -d "text=測試訊息：DNS 監控系統就緒"
```

群組或私訊收到「測試訊息」 = 成功 ✅

## Step 5：填進設定檔

編輯 `config/config.yaml`：

```yaml
telegram_enabled: true
telegram_bot_token: "7654321098:AAH-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
telegram_chat_id: "-1001234567890"
```

`chat_id` 加引號（YAML 對純數字會解析成整數，可能出錯）。

## Step 6：重啟服務

```bash
sudo systemctl restart dnscheck
```

第一輪檢測還不會發告警（因為沒有歷史狀態可比對），**第二輪開始**有變化才會發。

## 進階：客製化告警

`scripts/telegram_notify.py` 預設有這些行為：
- 5 分鐘內相同訊息只發一次（防洗版）
- 只在「狀態變化」時發（從 ok→blocked 或 blocked→ok）

想改的話編輯這個檔案：

```python
# 調整去重時間（秒）
_DEDUP_SECONDS = 300  # 改成 600 就是 10 分鐘去重

# 想加 emoji / Markdown 格式
await client.post(
    f"https://api.telegram.org/bot{token}/sendMessage",
    json={
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"  # 加這行可以用 **粗體** *斜體*
    }
)
```

## 常見問題

**Q: 一直收不到訊息**
1. 確認 bot 已加入群組
2. 確認 bot 是管理員
3. chat_id 對嗎（群組是負數）
4. 群組權限：群組設定 → 權限 → 確認 bot 能發訊息
5. 看 VPS log：`journalctl -u dnscheck | grep -i telegram`

**Q: 告警太多想關掉某些**
編輯 `scripts/checker.py` 裡 `await send_alert(...)` 那段，加條件判斷，例如：
```python
if change and cfg.get("telegram_enabled"):
    # 只報越南 ISP 的告警，菲律賓忽略
    if result["country"] == "VN":
        await send_alert(cfg, msg)
```

**Q: 想分群組（重要告警一個群、一般狀態另一個）**
複製 telegram_notify.py 改成多 chat_id 版本，或者用 Telegram 的 Topic 功能（一個群分多個話題）。

**Q: 同事手機沒有收到訊息**
Telegram 預設可能會把 bot 訊息歸類到「Archive」，請他們：
- 在群組設定打開「通知」
- 把群組釘選到頂部
