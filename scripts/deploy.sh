#!/bin/bash
# VPS 一鍵部署腳本（以 root 執行）
set -e

echo "=== DNS 檢測系統 VPS 部署腳本 ==="

# 1. 系統套件
echo "[1/6] 安裝系統套件..."
apt update
apt install -y python3 python3-pip python3-venv git sqlite3 nginx ufw curl

# 2. 建立使用者
echo "[2/6] 建立 dnscheck 使用者..."
if ! id dnscheck &>/dev/null; then
    useradd -m -s /bin/bash dnscheck
fi
mkdir -p /var/log/dnscheck /home/dnscheck/data /home/dnscheck/backup
chown -R dnscheck:dnscheck /var/log/dnscheck /home/dnscheck/data /home/dnscheck/backup

# 3. Tailscale
echo "[3/6] 安裝 Tailscale..."
if ! command -v tailscale &>/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
fi
echo ">>> 請執行 'tailscale up' 並用瀏覽器登入 Tailscale 帳號"
echo ">>> 完成後按 Enter 繼續..."
read

# 4. 部署專案（假設已在 /home/dnscheck/dns-check）
echo "[4/6] 安裝 Python 依賴..."
PROJECT_DIR="/home/dnscheck/dns-check"
if [ ! -d "$PROJECT_DIR" ]; then
    echo "錯誤：請先把專案放到 $PROJECT_DIR"
    exit 1
fi

sudo -u dnscheck bash <<EOF
cd $PROJECT_DIR
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
EOF

# 5. 設定檔
echo "[5/6] 設定檔..."
if [ ! -f "$PROJECT_DIR/config/config.yaml" ]; then
    cp "$PROJECT_DIR/config/config.yaml.example" "$PROJECT_DIR/config/config.yaml"
    chown dnscheck:dnscheck "$PROJECT_DIR/config/config.yaml"
    echo ">>> 設定檔已建立：$PROJECT_DIR/config/config.yaml"
    echo ">>> 請編輯該檔填入手機 Tailscale IP 和 Telegram Token"
fi

# 6. systemd
echo "[6/6] 註冊 systemd 服務..."
cp "$PROJECT_DIR/systemd/"*.service /etc/systemd/system/
cp "$PROJECT_DIR/systemd/"*.timer /etc/systemd/system/
systemctl daemon-reload

echo ""
echo "=== 部署完成 ==="
echo ""
echo "下一步："
echo "1. 編輯設定檔: nano $PROJECT_DIR/config/config.yaml"
echo "2. 編輯域名清單: nano $PROJECT_DIR/config/domains.txt"
echo "3. 測試健康檢查: sudo -u dnscheck $PROJECT_DIR/venv/bin/python $PROJECT_DIR/scripts/health_check.py"
echo "4. 啟動服務: systemctl enable --now dnscheck dnscheck-health.timer"
echo "5. 看 log: journalctl -u dnscheck -f"
