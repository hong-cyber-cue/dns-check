# Grafana Dashboard 匯入

## 一次性匯入（3 分鐘）

### Step 1：先確認 SQLite plugin 已裝
```bash
grafana-cli plugins install frser-sqlite-datasource
systemctl restart grafana-server
```

### Step 2：建立 SQLite 資料來源

1. 瀏覽器打開 Grafana：`http://your_vps_ip:3000`（預設 admin/admin）
2. 左側選單 → **Connections** → **Add new connection**
3. 搜尋 `SQLite` → 點選 → **Add new data source**
4. 設定：
   - **Name**：`dns-monitor`（很重要，待會匯入面板會用到）
   - **Path**：`/home/dnscheck/data/results.db`
5. **Save & test** → 應該看到綠色勾勾

### Step 3：匯入這份 Dashboard

1. 左側選單 → **Dashboards** → **New** → **Import**
2. 點 **Upload JSON file** → 選 `dashboard.json`
3. 看到 `Select a SQLite data source` 欄位 → 選剛剛建立的 `dns-monitor`
4. **Import** → 完成！

### Step 4：之後怎麼用

- 把這個 Dashboard 加入「Starred」（按右上角星星），下次直接從首頁進
- 預設每 1 分鐘自動刷新（右上角可以改）
- 預設顯示最近 24 小時，可以改成 7 天 / 30 天

## 面板說明

匯入後會看到 8 個面板：

1. **域名 × ISP 狀態矩陣**：主畫面，當前所有域名在所有 ISP 的狀態
2. **各 ISP 封鎖率**：哪個 ISP 封最多
3. **封鎖率趨勢**：過去 7 天每小時的變化
4. **最近 24 小時狀態變化**：哪些域名變紅了
5. **監測中域名數**：總覽
6. **監測中 ISP 數**：總覽
7. **上次檢測**：超過 60 分鐘變黃，超過 120 分鐘變紅（代表系統可能掛了）
8. **整體封鎖率**：所有 ISP 的平均

## 常見問題

**Q: 匯入後面板都顯示「No data」**
A: 檢測腳本還沒跑過，等下一輪檢測完就會有資料。或者 SQLite 路徑寫錯。

**Q: 矩陣表的 ISP 欄位是空的**
A: 你的 `config.yaml` 裡的 ISP 名稱跟 dashboard.json 裡的不一樣。要嘛改 config 跟 dashboard 對齊，要嘛編輯 dashboard 改 SQL 裡的 ISP 名稱。

**Q: 想加新 ISP 怎麼辦**
A: 編輯 `dashboard.json` 裡第一個面板的 SQL，加上一行：
```sql
MAX(CASE WHEN isp='新ISP名' THEN status END) as 新ISP名,
```
重新匯入即可（會覆蓋舊的）。

**Q: 想加新面板**
A: 進 Dashboard → 右上角 **Add** → **Visualization**，自己拉。
