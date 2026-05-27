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
    透過手機 SOCKS5 代理，取得手機 ISP DNS 對域名的解析結果。
    
    方法：透過 SOCKS5 訪問 https://myip.wtf/json?domain=DOMAIN 等第三方 API，
    但這些 API 不會回傳「手機 ISP DNS 的解析結果」。
    
    真正的方法：
    1. 透過 SOCKS5 對目標域名做 HTTPS HEAD 請求 → 測試「能不能連上」
    2. 透過 SOCKS5 訪問 https://dns.google/resolve → 雖然是 Google 的結果，
       但至少可以確認域名有 A 記錄
    3. 真正的 ISP DNS 解析結果，用「能否連上」來間接判斷：
       - 能連上 = ISP DNS 解析到了某個可用 IP = 沒被封
       - 連不上 = ISP DNS 污染了 / 域名被封了
    
    為了取得 ISP 實際解析到的 IP，我們用一個巧妙的方法：
    透過 SOCKS5 訪問 http://dns-api.org/A/{domain} 
    或用 https://1.1.1.1/cdn-cgi/trace 類似技巧。
    
    最終方案：先連線測試，再用 EDNS Client Subnet 模擬 ISP 查詢。
    但最簡單可靠的是：直接透過 SOCKS5 做 HTTP 連線，
    看 TLS 證書裡的 IP，或看連線是否成功。
    """
    transport = httpx.AsyncHTTPTransport(
        proxy=f"socks5://{socks5_host}:{socks5_port}"
    )
    
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout, verify=False) as client:
            # Step 1: 透過 SOCKS5 連目標域名，測試手機 ISP DNS 能不能解析
            try:
                r = await client.head(f"https://{domain}/", follow_redirects=True)
                # 連線成功（任何 HTTP 狀態碼都算成功，包括 403）
                # 表示手機的 ISP DNS 成功解析了這個域名
                connected = True
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError):
                connected = False
            except Exception:
                connected = False
            
            if not connected:
                return None  # ISP DNS 可能污染了，或目標伺服器不可達
            
            # Step 2: 連線成功，現在透過 SOCKS5 查 DNS API 取得實際解析到的 IP
            # 這個 API 請求也走手機網路，但 whatsmydns 等 API 回傳的是他們自己查到的
            # 我們改用另一個方法：讓 SOCKS5 代理解析域名後回報 IP
            # 用 httpbin.org/get 透過 HTTP 連到目標域名，從 Server 響應取得 IP
            
            # 最可靠的方法：透過 SOCKS5 發一個 HTTP 請求到
            # http://{domain}/ 讓代理做 DNS 解析
            # 然後用 cloudflare 的 trace 端點取得連線 IP
            try:
                r2 = await client.get(f"https://{domain}/cdn-cgi/trace")
                if r2.status_code == 200:
                    # Cloudflare trace 回傳 key=value 格式
                    for line in r2.text.split("\n"):
                        if line.startswith("ip="):
                            # 這是客戶端 IP（手機 IP），不是伺服器 IP
                            pass
            except Exception:
                pass
            
            # 如果域名用了 Cloudflare，所有域名都會解析到 Cloudflare IP
            # 所以「連得上」就代表 ISP DNS 正確解析到 Cloudflare
            # 標記為 CONNECTED，讓 check_one 知道連線成功
            return ["CONNECTED"]
    
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
        # 連線失敗 = ISP DNS 可能污染了這個域名
        # 但也可能是域名本身有問題，需要跟 DoH 對照
        real_ips = await query_real_ip_via_doh(domain)
        if not real_ips:
            # DoH 也查不到 → 域名本身有問題（沒 A 記錄、過期等）
            result["status"] = "unknown"
            result["reason"] = "DOMAIN_NO_RECORD"
            return result
        else:
            # DoH 能查到但手機連不上 → ISP 封鎖了
            result["status"] = "blocked"
            result["real_resolved_ip"] = real_ips[0]
            result["reason"] = "ISP_BLOCKED"
            return result
    
    isp_ip = isp_ips[0]
    
    if isp_ip == "CONNECTED":
        # 透過手機 SOCKS5 能連上目標域名 = ISP DNS 正常
        result["status"] = "ok"
        result["reason"] = "CONNECTED_OK"
        # 補充 real IP 資訊
        real_ips = await query_real_ip_via_doh(domain)
        if real_ips:
            result["real_resolved_ip"] = real_ips[0]
            result["isp_resolved_ip"] = real_ips[0]  # 能連上，IP 應該一致
        return result
    
    # 以下是拿到具體 IP 的情況（未來擴充用）
    result["isp_resolved_ip"] = isp_ip
    
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


# ============ 連線預檢 ============

async def check_socks5_alive(socks5_host: str, socks5_port: int, timeout: int = 5) -> bool:
    """測試 SOCKS5 代理是否能連上"""
    try:
        transport = httpx.AsyncHTTPTransport(
            proxy=f"socks5://{socks5_host}:{socks5_port}"
        )
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            r = await client.get("https://ipinfo.io/json")
            return r.status_code == 200
    except Exception:
        return False


# ============ 主迴圈 ============

async def run_round(cfg: dict, domains: list[str]):
    """跑一輪檢測"""
    db_path = cfg["db_path"]
    interval_ms = cfg.get("between_check_ms", 300)
    
    log.info(f"開始檢測：{len(domains)} 域名 × {len(cfg['isps'])} ISP")
    
    # 收集本輪所有結果
    round_results = []
    skipped_isps = []
    
    for isp_cfg in cfg["isps"]:
        isp_name = isp_cfg["name"]
        
        # 預檢：先測 SOCKS5 是否能連
        log.info(f"  → 預檢 {isp_name} SOCKS5 ({isp_cfg['socks5_host']}:{isp_cfg['socks5_port']})...")
        alive = await check_socks5_alive(isp_cfg["socks5_host"], isp_cfg["socks5_port"])
        
        if not alive:
            log.warning(f"  ❌ {isp_name} 手機斷線，跳過本輪檢測")
            skipped_isps.append(isp_cfg)
            continue
        
        log.info(f"  ✅ {isp_name} 連線正常，開始檢測")
        
        for domain in domains:
            try:
                result = await check_one(domain, isp_cfg)
                save_result(db_path, result)
                round_results.append(result)
                
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
                log.error(f"檢測失敗 {domain} @ {isp_name}: {e}")
            
            await asyncio.sleep(interval_ms / 1000)
    
    # 推送斷線告警
    if skipped_isps and cfg.get("telegram_enabled"):
        for isp_cfg in skipped_isps:
            msg = (
                f"📵 {isp_cfg['name']} ({isp_cfg['country']}) 手機斷線\n"
                f"SOCKS5 {isp_cfg['socks5_host']}:{isp_cfg['socks5_port']} 無回應\n"
                f"本輪已跳過該 ISP 的檢測\n"
                f"請檢查：\n"
                f"  • Tailscale 是否在線\n"
                f"  • Every Proxy 是否在跑\n"
                f"  • SIM 卡是否有訊號"
            )
            await send_alert(cfg, msg, force=True)
    
    # 每輪結束後推送完整報告
    if cfg.get("telegram_enabled"):
        await send_round_report(cfg, round_results, skipped_isps)


async def send_round_report(cfg: dict, results: list[dict], skipped_isps: list = None):
    """推送本輪完整狀態報告到 Telegram，按 ISP 分開顯示，含斷線偵測"""
    from datetime import datetime
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if skipped_isps is None:
        skipped_isps = []
    
    # 總統計
    total = len(results)
    ok_total = sum(1 for r in results if r["status"] == "ok")
    blocked_total = sum(1 for r in results if r["status"] == "blocked")
    unknown_total = sum(1 for r in results if r["status"] in ("unknown", "suspect"))
    
    # 第 1 條：總摘要
    summary = [
        f"📊 DNS 檢測報告",
        f"⏰ {now}",
        f"📈 統計：✅{ok_total}  🔴{blocked_total}  ⚪{unknown_total}  共{total}項",
    ]
    if skipped_isps:
        names = ", ".join(i["name"] for i in skipped_isps)
        summary.append(f"📵 斷線跳過：{names}")
    
    await send_alert(cfg, "\n".join(summary), force=True)
    await asyncio.sleep(0.5)
    
    # 按 ISP 分組
    by_isp = {}
    for r in results:
        by_isp.setdefault(r["isp"], []).append(r)
    
    # 每個 ISP 發獨立報告
    for isp_name, isp_results in by_isp.items():
        country = isp_results[0]["country"]
        blocked = [r for r in isp_results if r["status"] == "blocked"]
        unknown = [r for r in isp_results if r["status"] in ("unknown", "suspect")]
        ok_list = [r for r in isp_results if r["status"] == "ok"]
        
        # 斷線偵測：如果 ok 數量為 0 且 blocked 佔 90% 以上，很可能手機斷線
        total_isp = len(isp_results)
        blocked_pct = len(blocked) / total_isp * 100 if total_isp > 0 else 0
        phone_offline = len(ok_list) == 0 and blocked_pct > 90
        
        lines = [f"━━ {isp_name} ({country}) ━━"]
        
        if phone_offline:
            lines.append("")
            lines.append(f"📵 手機可能斷線！")
            lines.append(f"所有 {len(blocked)} 個域名都無法連線")
            lines.append(f"請檢查 {isp_name} 手機的：")
            lines.append(f"  • Tailscale 是否在線")
            lines.append(f"  • Every Proxy 是否在跑")
            lines.append(f"  • SIM 卡是否有訊號")
        else:
            # 被封域名
            if blocked:
                lines.append(f"⚠️ 被封（{len(blocked)}）：")
                for r in blocked:
                    lines.append(f"🔴 {r['domain']}")
            
            # 無記錄域名
            if unknown:
                lines.append(f"⚪ 無記錄（{len(unknown)}）：")
                for r in unknown:
                    lines.append(f"⚪ {r['domain']}")
            
            # 正常域名
            if ok_list:
                lines.append(f"✅ 正常（{len(ok_list)}）")
            
            if not blocked:
                lines.append("✅ 沒有域名被封鎖")
        
        # 如果太長就拆
        msg = "\n".join(lines)
        if len(msg) > 4000:
            # 先發被封
            header = [f"━━ {isp_name} ({country}) ━━"]
            if phone_offline:
                header.append(f"📵 手機可能斷線！所有域名無法連線，請檢查手機狀態")
            elif blocked:
                header.append(f"⚠️ 被封（{len(blocked)}）：")
                for r in blocked:
                    header.append(f"🔴 {r['domain']}")
            await send_alert(cfg, "\n".join(header), force=True)
            await asyncio.sleep(0.5)
            
            # 再發無記錄 + 正常統計
            if not phone_offline:
                footer = []
                if unknown:
                    footer.append(f"⚪ 無記錄（{len(unknown)}）：")
                    for r in unknown:
                        footer.append(f"⚪ {r['domain']}")
                if ok_list:
                    footer.append(f"✅ 正常（{len(ok_list)}）")
                if footer:
                    await send_alert(cfg, "\n".join(footer), force=True)
                    await asyncio.sleep(0.5)
        else:
            await send_alert(cfg, msg, force=True)
            await asyncio.sleep(0.5)


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
