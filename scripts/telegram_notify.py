"""Telegram 告警模組"""
import httpx
import logging

log = logging.getLogger(__name__)

# 防止洗版的簡易去重：相同訊息 5 分鐘內只發一次
_recent: dict[str, float] = {}
_DEDUP_SECONDS = 300


async def send_alert(cfg: dict, message: str, force: bool = False):
    import time
    token = cfg.get("telegram_bot_token")
    chat_id = cfg.get("telegram_chat_id")
    if not token or not chat_id:
        return
    
    now = time.time()
    # 去重（force=True 時跳過，用於每輪報告）
    if not force:
        key = message[:100]
        last = _recent.get(key, 0)
        if now - last < _DEDUP_SECONDS:
            return
        _recent[key] = now
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message}
            )
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")
