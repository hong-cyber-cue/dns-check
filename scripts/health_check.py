#!/usr/bin/env python3
"""
每小時跑一次，確認每台手機的 SOCKS5 還活著、且出口 IP 確實是當地 ISP
如果某個 ISP 代理掛了，發 Telegram 告警
"""

import asyncio
import yaml
import logging
import sys
from pathlib import Path

import httpx
import socks
import socket

sys.path.insert(0, str(Path(__file__).parent))
from telegram_notify import send_alert

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)


async def check_exit_ip(socks5_host: str, socks5_port: int) -> dict:
    """透過 SOCKS5 查 ipinfo.io，拿到出口 IP 與 ASN"""
    try:
        transport = httpx.AsyncHTTPTransport(
            proxy=f"socks5://{socks5_host}:{socks5_port}"
        )
        async with httpx.AsyncClient(transport=transport, timeout=15) as client:
            r = await client.get("https://ipinfo.io/json")
            return r.json()
    except Exception as e:
        return {"error": str(e)}


async def main():
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    
    failures = []
    for isp in cfg["isps"]:
        info = await check_exit_ip(isp["socks5_host"], isp["socks5_port"])
        
        if "error" in info:
            msg = f"❌ {isp['name']} SOCKS5 連線失敗: {info['error']}"
            log.error(msg)
            failures.append(msg)
            continue
        
        actual_org = info.get("org", "")
        actual_country = info.get("country", "")
        expected_keywords = isp.get("expected_org_keywords", [])
        expected_country = isp.get("country", "")
        
        log.info(
            f"{isp['name']}: IP={info.get('ip')} "
            f"country={actual_country} org={actual_org}"
        )
        
        # 國家檢查
        if actual_country != expected_country:
            msg = (
                f"⚠️ {isp['name']} 出口國家異常\n"
                f"預期: {expected_country}, 實際: {actual_country}\n"
                f"IP: {info.get('ip')}, Org: {actual_org}"
            )
            log.warning(msg)
            failures.append(msg)
            continue
        
        # ISP 關鍵字檢查
        if expected_keywords:
            if not any(kw.lower() in actual_org.lower() for kw in expected_keywords):
                msg = (
                    f"⚠️ {isp['name']} 出口 ISP 異常\n"
                    f"預期關鍵字: {expected_keywords}\n"
                    f"實際 Org: {actual_org}\n"
                    f"IP: {info.get('ip')}"
                )
                log.warning(msg)
                failures.append(msg)
    
    if failures:
        await send_alert(cfg, "🩺 健康檢查發現問題:\n\n" + "\n\n".join(failures))
    else:
        log.info("✅ 所有 SOCKS5 出口正常")


if __name__ == "__main__":
    asyncio.run(main())
