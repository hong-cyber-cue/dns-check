#!/usr/bin/env python3
"""
CLI 查詢工具 - 不用 Grafana 也能看當前狀態

用法：
  ./status.py             # 印出最新狀態矩陣
  ./status.py --history   # 印出最近 24 小時的變化
  ./status.py --isp Viettel  # 只看某個 ISP
  ./status.py --domain pbv88.com  # 只看某個域名
  ./status.py --blocked   # 只顯示被封的
  ./status.py --json      # 以 JSON 輸出（給其他程式用）
"""

import sqlite3
import sys
import argparse
import json
import yaml
from pathlib import Path
from datetime import datetime, timedelta

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

# ANSI 顏色
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
GRAY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"

STATUS_DISPLAY = {
    "ok": f"{GREEN}✓ OK   {RESET}",
    "blocked": f"{RED}✗ BLOCK{RESET}",
    "unknown": f"{GRAY}? UNKN {RESET}",
    "suspect": f"{YELLOW}! SUSP {RESET}",
}


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_latest_status(db_path: str, filters: dict = None) -> list:
    """取得每個 (domain, isp) 組合的最新狀態"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    query = """
        SELECT r.*
        FROM results r
        INNER JOIN (
            SELECT domain, isp, MAX(check_time) as max_time
            FROM results
            GROUP BY domain, isp
        ) latest 
        ON r.domain = latest.domain 
        AND r.isp = latest.isp 
        AND r.check_time = latest.max_time
    """
    
    conditions = []
    params = []
    if filters:
        if filters.get("isp"):
            conditions.append("r.isp = ?")
            params.append(filters["isp"])
        if filters.get("domain"):
            conditions.append("r.domain = ?")
            params.append(filters["domain"])
        if filters.get("blocked_only"):
            conditions.append("r.status = 'blocked'")
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY r.domain, r.isp"
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_changes(db_path: str, hours: int = 24) -> list:
    """取得最近 N 小時的狀態變化"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    since = int(datetime.now().timestamp()) - hours * 3600
    
    # 找出每個 (domain, isp) 在這段時間內所有狀態
    query = """
        SELECT domain, isp, check_time, status, reason, isp_resolved_ip
        FROM results
        WHERE check_time > ?
        ORDER BY domain, isp, check_time
    """
    rows = conn.execute(query, (since,)).fetchall()
    conn.close()
    
    # 找出狀態變化點
    changes = []
    prev = {}
    for r in rows:
        key = (r["domain"], r["isp"])
        if key in prev and prev[key] != r["status"]:
            changes.append({
                "time": r["check_time"],
                "domain": r["domain"],
                "isp": r["isp"],
                "from": prev[key],
                "to": r["status"],
                "reason": r["reason"],
                "isp_ip": r["isp_resolved_ip"],
            })
        prev[key] = r["status"]
    
    return list(reversed(changes))  # 最新的在前


def print_matrix(rows: list, isps: list[str]):
    """印出域名 × ISP 矩陣"""
    if not rows:
        print(f"{YELLOW}沒有資料{RESET}")
        return
    
    # 整理成 dict[domain][isp] = row
    matrix = {}
    for r in rows:
        matrix.setdefault(r["domain"], {})[r["isp"]] = r
    
    # 計算 domain 欄位寬度
    domain_w = max(len(d) for d in matrix.keys())
    domain_w = max(domain_w, 15)
    
    # 表頭
    header = f"{BOLD}{'域名':<{domain_w}}{RESET}  "
    for isp in isps:
        header += f"{BOLD}{isp:<8}{RESET}"
    print(header)
    print("─" * (domain_w + 2 + 8 * len(isps)))
    
    # 內容
    for domain in sorted(matrix.keys()):
        row_str = f"{domain:<{domain_w}}  "
        for isp in isps:
            cell = matrix[domain].get(isp)
            if cell is None:
                row_str += f"{GRAY}—       {RESET}"
            else:
                row_str += STATUS_DISPLAY.get(cell["status"], cell["status"])
        print(row_str)
    
    # 統計
    total = 0
    blocked = 0
    for d in matrix.values():
        for cell in d.values():
            total += 1
            if cell["status"] == "blocked":
                blocked += 1
    
    print()
    print(f"統計：共 {total} 項，被封鎖 {blocked} 項（{100*blocked/total:.1f}%）")
    
    # 最後檢測時間
    last_time = max(r["check_time"] for r in rows)
    last_dt = datetime.fromtimestamp(last_time)
    ago = (datetime.now() - last_dt).total_seconds() / 60
    print(f"最後檢測：{last_dt.strftime('%Y-%m-%d %H:%M:%S')}（{ago:.0f} 分鐘前）")


def print_history(changes: list, hours: int):
    """印出歷史變化"""
    if not changes:
        print(f"{GREEN}最近 {hours} 小時沒有狀態變化{RESET}")
        return
    
    print(f"{BOLD}最近 {hours} 小時的狀態變化（{len(changes)} 次）：{RESET}")
    print()
    
    for c in changes:
        dt = datetime.fromtimestamp(c["time"]).strftime("%m-%d %H:%M")
        arrow_color = RED if c["to"] == "blocked" else (GREEN if c["to"] == "ok" else YELLOW)
        print(
            f"  {GRAY}{dt}{RESET}  "
            f"{c['domain']:<20}  @  {c['isp']:<10}  "
            f"{c['from']} {arrow_color}→ {c['to']}{RESET}  "
            f"{GRAY}{c.get('reason', '')}{RESET}"
        )


def main():
    parser = argparse.ArgumentParser(description="DNS 檢測結果查詢工具")
    parser.add_argument("--history", action="store_true", help="顯示歷史變化")
    parser.add_argument("--hours", type=int, default=24, help="歷史時間範圍（小時）")
    parser.add_argument("--isp", help="只看某個 ISP")
    parser.add_argument("--domain", help="只看某個域名")
    parser.add_argument("--blocked", action="store_true", help="只看被封的")
    parser.add_argument("--json", action="store_true", help="JSON 輸出")
    args = parser.parse_args()
    
    cfg = load_config()
    db_path = cfg["db_path"]
    isps = [i["name"] for i in cfg["isps"]]
    
    if not Path(db_path).exists():
        print(f"{RED}資料庫不存在：{db_path}{RESET}")
        print("檢測腳本還沒跑過，或者路徑錯了")
        sys.exit(1)
    
    if args.history:
        changes = get_recent_changes(db_path, args.hours)
        if args.json:
            print(json.dumps(changes, ensure_ascii=False, indent=2))
        else:
            print_history(changes, args.hours)
    else:
        filters = {
            "isp": args.isp,
            "domain": args.domain,
            "blocked_only": args.blocked,
        }
        rows = get_latest_status(db_path, filters)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            display_isps = [args.isp] if args.isp else isps
            print_matrix(rows, display_isps)


if __name__ == "__main__":
    main()
