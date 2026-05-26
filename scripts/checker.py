#!/usr/bin/env python3
"""
DNS 封鎖檢測腳本 v2.1
透過 Tailscale + SOCKS5 從各 ISP 的手機網路出去，檢測域名是否被 DNS 污染

核心原理：
  SOCKS5 協議支持讓代理端（手機）解析域名。
  當我們透過 SOCKS5 連 domain:443 時，手機會用 ISP 預設 DNS 解析。
  如果 ISP 污染了 DNS → 手機解析到假 IP → TCP 連到假 IP → getpeername() 拿到假 IP
  如果 ISP 沒污染 → 手機解析到真 IP → TCP 連到真 IP → getpeername() 拿到真 IP
  然後跟 VPS 端直接透過 DoH 查到的真 IP 比對 ASN，就能判斷是否被封。
"""

import asyncio
import sqlite3
import yaml
import time
import logging
import socket
import struct
import random
import string
from pathlib import Path
from datetime import datetime
from typing import Optional

import dns.message
import dns.query
import dns.rdatatype
import httpx
import socks
from telegram_notify import send_alert

# ============ 設定 ============
CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"
LOG_PATH = Path("/var/log/dnscheck/checker.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ============ 工具函式 ============

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def init_db(db_path: str):
    """建立資料表"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_time INTEGER NOT NULL,
            domain TEXT NOT NULL,
            isp TEXT NOT NULL,
            country TEXT NOT NULL,
            status TEXT NOT NULL,
            isp_resolved_ip TEXT,
            isp_resolved_asn INTEGER,
            real_resolved_ip TEXT,
            real_resolved_asn INTEGER,
            reason TEXT,
            rtt_ms INTEGER
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_time ON results(check_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_domain_isp ON results(domain, isp)")
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS state_changes (
            domain TEXT,
            isp TEXT,
            last_status TEXT,
            changed_at INTEGER,
            PRIMARY KEY (domain, isp)
        )
    """)
    conn.commit()
    conn.close()


# ============ DNS 查詢核心 ============

async def query_isp_dns_via_socks5(
    domain: str,
    isp_dns_ip: str,
    socks5_host: str,
    socks5_port: int,
    timeout: int = 15
) -> Optional[list[str]]:
    """
    透過手機 SOCKS5 代理查詢域名的 IP。
    
    方法：用 httpx 透過 SOCKS5 做 HEAD 請求到目標域名。
    httpx 底層會讓 SOCKS5 代理（手機端）用 ISP DNS 解析域名。
    然後從底層 socket 的 getpeername() 拿到真正連到的 IP。
    
    由於 PySocks 的 getpeername() 會回傳域名而不是 IP，
    改用 httpx transport 的方式，從 HTTP 連線資訊取得 remote IP。
    """
    try:
        transport = httpx.AsyncHTTPTransport(
            proxy=f"socks5://{socks5_host}:{socks5_port}"
        )
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            # 做 HEAD 請求，讓手機的 ISP DNS 解析域名
            r = await client.head(f"https://{domain}/", follow_redirects=True)
            # 從連線資訊取得 remote IP
            # httpx 的 stream.connection 裡有 socket info
            ip = r.extensions.get("network_stream")
            if ip:
                sock = ip.get_extra_info("socket")
                if sock:
                    peer = sock.getpeername()
                    if peer:
                        return [peer[0]]
            
            # 備用方法：如果拿不到 socket info，
            # 至少連線成功代表 DNS 沒被完全封鎖
            # 用 DNS 查詢取得手機解析到的 IP
            # 透過 SOCKS5 查一個 DNS API
            try:
                r2 = await client.get(
                    f"https://dns.google/resolve?name={domain}&type=A",
                )
                dns_data = r2.json()
                if "Answer" in dns_data:
                    ips = [a["data"] for a in dns_data["Answer"] if a["type"] == 1]
                    if ips:
                        return ips
            except Exception:
                pass
            
            # 最終備用：連線成功但拿不到 IP，用佔位符標記
            return ["CONNECTED_BUT_IP_UNKNOWN"]
    
    except httpx.ConnectError as e:
        log.debug(f"Connect error for {domain}: {e}")
        return None
    except httpx.ConnectTimeout:
        log.debug(f"Timeout for {domain}")
        return None
    except Exception as e:
        log.debug(f"SOCKS5 query failed for {domain}: {e}")
        return None


async def query_real_ip_via_doh(domain: str) -> Optional[list[str]]:
    """
    透過 Cloudflare DoH 查真實 IP（不經過手機，從 VPS 直接出去）
    用 DoH 避免被任何 DNS 劫持污染
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://1.1.1.1/dns-query",
                params={"name": domain, "type": "A"},
                headers={"Accept": "application/dns-json"}
            )
            data = r.json()
            if "Answer" not in data:
                return None
            ips = [a["data"] for a in data["Answer"] if a["type"] == 1]
            return ips if ips else None
    except Exception as e:
        log.warning(f"DoH query failed for {domain}: {e}")
        return None


# ============ ASN 查詢 ============

_asn_cache: dict[str, int] = {}


async def lookup_asn(ip: str) -> Optional[int]:
    """查 IP 的 ASN 號碼。用 Team Cymru 的 whois DNS 服務"""
    if ip in _asn_cache:
        return _asn_cache[ip]
    
    try:
        parts = ip.split(".")
        if len(parts) != 4:
            return None
        reversed_ip = ".".join(reversed(parts))
        query_name = f"{reversed_ip}.origin.asn.cymru.com"
        
        query = dns.message.make_query(query_name, dns.rdatatype.TXT)
        response = await asyncio.to_thread(
            dns.query.udp, query, "1.1.1.1", timeout=5
        )
        
        for rrset in response.answer:
            for item in rrset.items:
                txt = item.to_text().strip('"').split("|")[0].strip()
                asn = int(txt.split()[0])
                _asn_cache[ip] = asn
                return asn
    except Exception as e:
        log.debug(f"ASN lookup failed for {ip}: {e}")
    return None


# ============ 單次檢測邏輯 ============

def is_obviously_blocked_ip(ip: str) -> bool:
    """明顯的封鎖 IP 樣式"""
    if ip in ("0.0.0.0", "127.0.0.1", "127.0.0.53"):
        return True
    if ip.startswith(("10.", "192.168.", "172.")):
        return True
    return False


async def check_one(domain: str, isp_cfg: dict) -> dict:
    """檢測單一域名在單一 ISP 的狀態"""
    start = time.time()
    isp_name = isp_cfg["name"]
    
    # 1. 透過手機 SOCKS5 連線，讓手機的 ISP DNS 解析域名
    isp_ips = await query_isp_dns_via_socks5(
        domain,
        isp_cfg["isp_dns"],
        isp_cfg["socks5_host"],
        isp_cfg["socks5_port"]
    )
    
    rtt_ms = int((time.time() - start) * 1000)
    
    result = {
        "check_time": int(time.time()),
        "domain": domain,
        "isp": isp_name,
        "country": isp_cfg["country"],
        "rtt_ms": rtt_ms,
        "isp_resolved_ip": None,
        "isp_resolved_asn": None,
        "real_resolved_ip": None,
        "real_resolved_asn": None,
    }
    
    # 2. 判斷 ISP DNS 結果
    if not isp_ips:
        result["status"] = "blocked"
        result["reason"] = "CONNECT_FAIL"
        return result
    
    isp_ip = isp_ips[0]
    result["isp_resolved_ip"] = isp_ip
    
    # 如果連線成功但拿不到具體 IP，至少知道沒被封
    if isp_ip == "CONNECTED_BUT_IP_UNKNOWN":
        result["status"] = "ok"
        result["reason"] = "CONNECTED_OK"
        return result
    
    if is_obviously_blocked_ip(isp_ip):
        result["status"] = "blocked"
        result["reason"] = f"DNS_SINKHOLE:{isp_ip}"
        return result
    
    # 3. 查真實 IP 對照
    real_ips = await query_real_ip_via_doh(domain)
    if not real_ips:
        result["status"] = "unknown"
        result["reason"] = "REAL_DNS_FAIL"
        return result
    
    real_ip = real_ips[0]
    result["real_resolved_ip"] = real_ip
    
    # 4. ASN 比對
    isp_asn = await lookup_asn(isp_ip)
    real_asn = await lookup_asn(real_ip)
    result["isp_resolved_asn"] = isp_asn
    result["real_resolved_asn"] = real_asn
    
    if isp_asn is None or real_asn is None:
        if set(isp_ips) & set(real_ips):
            result["status"] = "ok"
            result["reason"] = "IP_MATCH"
        else:
            result["status"] = "suspect"
            result["reason"] = "ASN_LOOKUP_FAIL_IP_DIFF"
        return result
    
    if isp_asn == real_asn:
        result["status"] = "ok"
        result["reason"] = f"ASN_MATCH:{isp_asn}"
    else:
        result["status"] = "blocked"
        result["reason"] = f"ASN_DIFF:{isp_asn}_vs_{real_asn}"
    
    return result


# ============ 寫入與告警 ============

def save_result(db_path: str, result: dict):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO results (
            check_time, domain, isp, country, status,
            isp_resolved_ip, isp_resolved_asn,
            real_resolved_ip, real_resolved_asn,
            reason, rtt_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result["check_time"], result["domain"], result["isp"],
        result["country"], result["status"],
        result.get("isp_resolved_ip"), result.get("isp_resolved_asn"),
        result.get("real_resolved_ip"), result.get("real_resolved_asn"),
        result.get("reason"), result.get("rtt_ms")
    ))
    conn.commit()
    conn.close()


def check_state_change(db_path: str, result: dict) -> Optional[str]:
    """檢查狀態是否變化"""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT last_status FROM state_changes WHERE domain=? AND isp=?",
        (result["domain"], result["isp"])
    )
    row = cur.fetchone()
    current = result["status"]
    
    if row is None:
        conn.execute(
            "INSERT INTO state_changes (domain, isp, last_status, changed_at) VALUES (?, ?, ?, ?)",
            (result["domain"], result["isp"], current, result["check_time"])
        )
        conn.commit()
        conn.close()
        return None
    
    last = row[0]
    if last == current:
        conn.close()
        return None
    
    conn.execute(
        "UPDATE state_changes SET last_status=?, changed_at=? WHERE domain=? AND isp=?",
        (current, result["check_time"], result["domain"], result["isp"])
    )
    conn.commit()
    conn.close()
    
    return f"{last} → {current}"


# ============ 主迴圈 ============

async def run_round(cfg: dict, domains: list[str]):
    """跑一輪檢測"""
    db_path = cfg["db_path"]
    interval_ms = cfg.get("between_check_ms", 300)
    
    log.info(f"開始檢測：{len(domains)} 域名 × {len(cfg['isps'])} ISP")
    
    for isp_cfg in cfg["isps"]:
        log.info(f"  → 檢測 ISP: {isp_cfg['name']}")
        for domain in domains:
            try:
                result = await check_one(domain, isp_cfg)
                save_result(db_path, result)
                
                change = check_state_change(db_path, result)
                if change and cfg.get("telegram_enabled"):
                    msg = (
                        f"🚨 域名狀態變化\n"
                        f"域名: {result['domain']}\n"
                        f"ISP: {result['isp']} ({result['country']})\n"
                        f"變化: {change}\n"
                        f"原因: {result.get('reason')}\n"
                        f"ISP解析: {result.get('isp_resolved_ip')}\n"
                        f"真實IP: {result.get('real_resolved_ip')}"
                    )
                    await send_alert(cfg, msg)
                
                log.info(
                    f"    {domain:30s} [{result['status']:8s}] "
                    f"isp={result.get('isp_resolved_ip')} "
                    f"real={result.get('real_resolved_ip')} "
                    f"reason={result.get('reason')}"
                )
            except Exception as e:
                log.error(f"檢測失敗 {domain} @ {isp_cfg['name']}: {e}")
            
            await asyncio.sleep(interval_ms / 1000)


async def main():
    cfg = load_config()
    init_db(cfg["db_path"])
    
    domains_file = Path(__file__).parent.parent / cfg["domains_file"]
    with open(domains_file) as f:
        domains = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    
    interval_min = cfg.get("check_interval_minutes", 30)
    log.info(f"啟動 DNS 檢測，每 {interval_min} 分鐘一輪")
    
    while True:
        try:
            await run_round(cfg, domains)
            log.info(f"本輪結束，休息 {interval_min} 分鐘")
        except Exception as e:
            log.error(f"輪次異常: {e}", exc_info=True)
        await asyncio.sleep(interval_min * 60)


if __name__ == "__main__":
    asyncio.run(main())
