"""ClawLink agent daemon — starts blank, owner instructs it to install and operate."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from typing import Any

import httpx
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("claw-agent")

# ── Config ───────────────────────────────────────────────────

RELAY_URL = os.environ.get("RELAY_URL", "https://claw-link-relay.fly.dev")
AGENT_NAME = os.environ.get("AGENT_NAME", "Claw Agent")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.moonshot.ai/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "kimi-k2.5")
MAX_ROUNDS_PER_FRIEND = int(os.environ.get("MAX_ROUNDS", "10"))
SOCIAL_RULES = os.environ.get("SOCIAL_RULES", """
你是一个AI助理，代表你的主人和其他AI助理沟通。
规则：
- 礼貌、专业、简洁
- 专注完成任务
- 不要泄露主人的隐私信息
- 任务完成或达成一致后，在回复末尾加上 [DONE]
- 对方说了 [DONE] 就不要再回复
- 每次回复必须包含实质内容
""".strip())

# ── State ────────────────────────────────────────────────────

_state = {
    "installed": False,
    "claw_id": "",
    "public_key": "",
    "private_key": "",
    "friends": {},
    "history": {},
    "rounds": {},
    "done": set(),
    "conversation_log": [],  # Owner-visible conversation log
}

_llm = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
_sse_task = None
_poll_task = None


# ── Owner conversation log ───────────────────────────────────

def log_to_owner(role: str, message: str):
    """Log a message to the owner-visible conversation."""
    _state["conversation_log"].append({"role": role, "message": message})
    prefix = "👤 Owner" if role == "owner" else "🦞 Agent"
    logger.info(f"[CHAT] {prefix}: {message[:200]}")


# ── LLM ──────────────────────────────────────────────────────

async def think(friend_id: str, message: str) -> str:
    history = _state["history"].setdefault(friend_id, [])
    history.append({"role": "user", "content": message})
    recent = history[-20:]
    try:
        resp = await _llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": f"You are {AGENT_NAME}.\n\n{SOCIAL_RULES}"},
                *recent,
            ],
            max_tokens=500,
        )
        reply = resp.choices[0].message.content or "..."
        history.append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return f"Sorry, I encountered an error: {type(e).__name__}"


# ── Installation ─────────────────────────────────────────────

def do_install():
    """Install ClawLink from PyPI and register."""
    log_to_owner("agent", "正在安装 ClawLink...")

    # pip install
    r = subprocess.run(["pip", "install", "clawlink-mcp", "--quiet"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log_to_owner("agent", f"安装失败: {r.stderr[:200]}")
        return False
    version = subprocess.run(["pip", "show", "clawlink-mcp"],
                             capture_output=True, text=True).stdout
    ver = [l for l in version.split('\n') if 'Version' in l]
    log_to_owner("agent", f"ClawLink 安装完成 ({ver[0].strip() if ver else '?'})")

    # Register
    r = subprocess.run(["claw-link", "init", "--name", AGENT_NAME],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log_to_owner("agent", f"注册失败: {r.stderr[:200]}")
        return False

    # Parse Claw ID from output
    claw_id = ""
    for line in r.stdout.split('\n'):
        if 'Claw ID:' in line:
            claw_id = line.split('Claw ID:')[1].strip()
    log_to_owner("agent", f"注册成功！我的 Claw ID: {claw_id}")

    # Load identity from disk
    from claw_link.storage import LocalStorage
    storage = LocalStorage()
    identity = storage.load_identity()
    cfg = storage.load_config()
    _state["claw_id"] = cfg.get("claw_id", claw_id)
    _state["public_key"] = identity["public_key"]
    _state["private_key"] = identity["private_key"]
    _state["installed"] = True

    # Verify
    r = subprocess.run(["claw-link", "status"], capture_output=True, text=True)
    log_to_owner("agent", f"状态验证:\n{r.stdout.strip()}")

    return True


# ── ClawLink operations ──────────────────────────────────────

async def sync_friends():
    claw_id = _state["claw_id"]
    if not claw_id:
        return
    try:
        from claw_link.client import RelayClient
        async with RelayClient(RELAY_URL) as client:
            remote = await client.list_friends(claw_id)
            for f in remote:
                fid = f.get("claw_id", "")
                if fid and fid not in _state["friends"]:
                    info = await client.get_claw(fid)
                    _state["friends"][fid] = {
                        "name": f.get("name", "Unknown"),
                        "public_key": info.get("public_key", ""),
                    }
                    logger.info(f"Synced friend: {f.get('name')} ({fid})")
    except Exception as e:
        logger.debug(f"Friend sync failed: {e}")


async def check_and_accept_requests():
    claw_id = _state["claw_id"]
    if not claw_id:
        return
    try:
        from claw_link.client import RelayClient
        async with RelayClient(RELAY_URL) as client:
            reqs = await client.get_friend_requests(claw_id)
            for req in reqs:
                rid = req.get("request_id", "")
                from_id = req.get("from_id", "")
                logger.info(f"Auto-accepting friend request from {from_id}")
                result = await client.accept_friend(claw_id, rid)
                friend_id = result.get("claw_id", "")
                if friend_id:
                    info = await client.get_claw(friend_id)
                    _state["friends"][friend_id] = {
                        "name": info.get("name", "Unknown"),
                        "public_key": info.get("public_key", ""),
                    }
                    log_to_owner("agent", f"收到好友请求并已自动接受: {info.get('name')} ({friend_id})")
    except Exception as e:
        logger.debug(f"Friend request check failed: {e}")


async def process_messages():
    claw_id = _state["claw_id"]
    if not claw_id:
        return
    try:
        from claw_link.client import RelayClient
        from claw_link.crypto import decrypt, encrypt
        async with RelayClient(RELAY_URL) as client:
            pending = await client.get_pending_messages(claw_id)
            for msg in pending:
                from_id = msg.get("from_id", "")
                friend = _state["friends"].get(from_id)
                if not friend:
                    await sync_friends()
                    friend = _state["friends"].get(from_id)
                msg_id = msg.get("message_id", "")
                if not friend or not friend.get("public_key"):
                    if msg_id:
                        await client.ack_message(msg_id)
                    continue
                try:
                    content = decrypt(msg.get("encrypted_payload", ""),
                                      friend["public_key"], _state["private_key"])
                except Exception:
                    if msg_id:
                        await client.ack_message(msg_id)
                    continue
                logger.info(f"Message from {friend['name']}: {content[:100]}")
                if msg_id:
                    await client.ack_message(msg_id)
                if from_id in _state["done"]:
                    continue
                if "[DONE]" in content:
                    _state["done"].add(from_id)
                    log_to_owner("agent", f"与 {friend['name']} 的对话已结束。对方最后说: {content[:100]}")
                    continue
                rounds = _state["rounds"].get(from_id, 0) + 1
                _state["rounds"][from_id] = rounds
                if rounds > MAX_ROUNDS_PER_FRIEND:
                    _state["done"].add(from_id)
                    final = "对话轮次已达上限，下次再继续。[DONE]"
                    encrypted = encrypt(final, friend["public_key"], _state["private_key"])
                    await client.send_message(claw_id, from_id, encrypted)
                    continue
                reply = await think(from_id, content)
                logger.info(f"Reply to {friend['name']}: {reply[:100]}")
                if "[DONE]" in reply:
                    _state["done"].add(from_id)
                    log_to_owner("agent", f"与 {friend['name']} 的对话完成。我的回复: {reply[:100]}")
                encrypted = encrypt(reply, friend["public_key"], _state["private_key"])
                await client.send_message(claw_id, from_id, encrypted)
    except Exception as e:
        logger.error(f"Message processing failed: {e}", exc_info=True)


# ── SSE + polling ────────────────────────────────────────────

async def sse_loop():
    claw_id = _state["claw_id"]
    sse_url = f"{RELAY_URL.rstrip('/')}/v1/events/{claw_id}/stream"
    while True:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", sse_url) as response:
                    response.raise_for_status()
                    logger.info("SSE connected")
                    async for line in response.aiter_lines():
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            payload = line[len("data:"):].strip()
                            if not payload:
                                continue
                            try:
                                event = json.loads(payload)
                            except json.JSONDecodeError:
                                continue
                            etype = event.get("type", "")
                            if etype == "new_message":
                                await process_messages()
                            elif etype == "friend_request":
                                await check_and_accept_requests()
                            elif etype == "friend_accepted":
                                await sync_friends()
                            elif etype == "connected":
                                logger.info("SSE stream active")
        except Exception as e:
            logger.warning(f"SSE error: {type(e).__name__}: {e}")
        await asyncio.sleep(5)


async def fallback_poll():
    while True:
        await asyncio.sleep(60)
        if _state["installed"]:
            try:
                await sync_friends()
                await check_and_accept_requests()
                await process_messages()
            except Exception as e:
                logger.debug(f"Poll error: {e}")


async def start_event_loops():
    """Start SSE and polling after installation."""
    global _sse_task, _poll_task
    await sync_friends()
    await check_and_accept_requests()
    await process_messages()
    _sse_task = asyncio.create_task(sse_loop())
    _poll_task = asyncio.create_task(fallback_poll())
    logger.info("Event loops started (SSE + polling)")


# ── HTTP API (Owner interface) ───────────────────────────────

async def http_server():
    from aiohttp import web

    async def handle_instruct(request: web.Request) -> web.Response:
        """Owner sends an instruction to the agent."""
        data = await request.json()
        instruction = data.get("message", "")
        if not instruction:
            return web.json_response({"error": "need 'message'"}, status=400)

        log_to_owner("owner", instruction)

        # If not installed yet, check if the instruction is about installation
        if not _state["installed"]:
            if any(kw in instruction for kw in ["安装", "install", "ClawLink", "clawlink", "setup"]):
                success = do_install()
                if success:
                    await start_event_loops()
                    log_to_owner("agent", "ClawLink 安装和配置全部完成！SSE 实时连接已建立。随时可以加好友和发消息。")
                return web.json_response({
                    "conversation": _state["conversation_log"],
                })
            else:
                log_to_owner("agent", "我还没有安装 ClawLink。请先让我安装：'请安装 ClawLink'")
                return web.json_response({"conversation": _state["conversation_log"]})

        # Already installed — handle other instructions
        if any(kw in instruction for kw in ["加好友", "add-friend", "add friend"]):
            # Extract claw ID from instruction
            import re
            match = re.search(r'claw_[a-z0-9]+', instruction)
            if match:
                friend_id = match.group(0)
                r = subprocess.run(["claw-link", "add-friend", friend_id],
                                   capture_output=True, text=True)
                log_to_owner("agent", f"好友请求已发送给 {friend_id}")
                # Wait a moment for auto-accept
                await asyncio.sleep(3)
                await sync_friends()
                friend_names = {k: v["name"] for k, v in _state["friends"].items()}
                if friend_id in friend_names:
                    log_to_owner("agent", f"对方已接受！当前好友: {friend_names}")
                else:
                    log_to_owner("agent", f"等待对方接受。当前好友: {friend_names}")
            else:
                log_to_owner("agent", "请提供好友的 Claw ID（格式: claw_xxxxxxxx）")
            return web.json_response({"conversation": _state["conversation_log"]})

        if any(kw in instruction for kw in ["发消息", "约", "问", "send", "tell", "ask", "帮我"]):
            # Find target friend
            import re
            match = re.search(r'claw_[a-z0-9]+', instruction)
            target_id = match.group(0) if match else None
            if not target_id and _state["friends"]:
                target_id = list(_state["friends"].keys())[0]
            if not target_id:
                log_to_owner("agent", "还没有好友，请先加好友。")
                return web.json_response({"conversation": _state["conversation_log"]})

            friend = _state["friends"].get(target_id)
            if not friend:
                log_to_owner("agent", f"{target_id} 不在好友列表中。")
                return web.json_response({"conversation": _state["conversation_log"]})

            # Reset conversation state
            _state["done"].discard(target_id)
            _state["rounds"][target_id] = 0
            _state["history"][target_id] = []

            log_to_owner("agent", f"好的，我来跟 {friend['name']} 沟通...")

            from claw_link.crypto import encrypt
            from claw_link.client import RelayClient
            reply = await think(target_id,
                f"[系统指令] 你的主人让你跟{friend['name']}说：{instruction}\n"
                f"请直接用对话的方式跟对方说，不要跟主人汇报。这是对话的开始。")
            encrypted = encrypt(reply, friend["public_key"], _state["private_key"])
            async with RelayClient(RELAY_URL) as client:
                await client.send_message(_state["claw_id"], target_id, encrypted)
            log_to_owner("agent", f"已发送给 {friend['name']}: {reply[:200]}")
            return web.json_response({"conversation": _state["conversation_log"]})

        log_to_owner("agent", f"收到指令: {instruction}。我可以帮你：安装ClawLink、加好友、发消息。")
        return web.json_response({"conversation": _state["conversation_log"]})

    async def handle_status(request: web.Request) -> web.Response:
        return web.json_response({
            "installed": _state["installed"],
            "claw_id": _state["claw_id"],
            "name": AGENT_NAME,
            "friends": {k: v["name"] for k, v in _state["friends"].items()},
        })

    async def handle_log(request: web.Request) -> web.Response:
        return web.json_response({"conversation": _state["conversation_log"]})

    app = web.AppRunner(web.Application(client_max_size=1024**2))
    app.app.router.add_post("/instruct", handle_instruct)
    app.app.router.add_get("/status", handle_status)
    app.app.router.add_get("/log", handle_log)
    # Keep /send for backward compat
    app.app.router.add_post("/send", handle_instruct)
    await app.setup()
    site = web.TCPSite(app, "0.0.0.0", 8080)
    await site.start()
    logger.info("Agent ready — waiting for owner instructions on :8080")


# ── Main ─────────────────────────────────────────────────────

async def main():
    if not LLM_API_KEY:
        logger.error("LLM_API_KEY is required")
        sys.exit(1)

    print(f"\n{'=' * 50}")
    print(f"  {AGENT_NAME}")
    print(f"  Status: Waiting for owner instructions")
    print(f"  ClawLink: NOT INSTALLED")
    print(f"{'=' * 50}\n")

    await http_server()

    # Just keep running, waiting for instructions
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
