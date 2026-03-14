"""Minimal ClawLink agent daemon — always-on brain that auto-responds via SSE."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

import httpx
from openai import AsyncOpenAI

from claw_link.client import RelayClient
from claw_link.crypto import generate_keypair, encrypt, decrypt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("claw-agent")

# ── Config from environment ──────────────────────────────────

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
- 不确定时说"我需要跟我主人确认一下"
- 对话要有目的，完成就结束
- 任务完成或达成一致后，在回复末尾加上 [DONE]
- 对方说了 [DONE] 就不要再回复，对话结束了
- 不要在目的达成后继续闲聊
- 每次回复必须包含实质内容
""".strip())

# ── State ────────────────────────────────────────────────────

_state = {
    "claw_id": "",
    "public_key": "",
    "private_key": "",
    "friends": {},  # {claw_id: {"name": str, "public_key": str}}
    "history": {},  # {claw_id: [{"role": str, "content": str}]}
    "rounds": {},   # {claw_id: int} — count of rounds per friend
    "done": set(),  # claw_ids where conversation is marked done
}


# ── LLM ──────────────────────────────────────────────────────

_llm = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)


async def think(friend_id: str, message: str) -> str:
    """Call LLM to generate a response."""
    history = _state["history"].setdefault(friend_id, [])
    history.append({"role": "user", "content": message})

    # Keep last 20 messages for context
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


# ── ClawLink operations ──────────────────────────────────────

async def register():
    """Register this agent on ClawLink."""
    kp = generate_keypair()
    _state["public_key"] = kp.public_key
    _state["private_key"] = kp.private_key

    async with RelayClient(RELAY_URL) as client:
        result = await client.register(AGENT_NAME, kp.public_key)
        _state["claw_id"] = result["claw_id"]
        logger.info(f"Registered as {AGENT_NAME} ({_state['claw_id']})")
        return _state["claw_id"]


async def sync_friends():
    """Sync friend list from relay."""
    claw_id = _state["claw_id"]
    if not claw_id:
        return
    try:
        async with RelayClient(RELAY_URL) as client:
            friends = await client.list_friends(claw_id)
            for f in friends:
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
    """Auto-accept all pending friend requests."""
    claw_id = _state["claw_id"]
    if not claw_id:
        return
    try:
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
                    logger.info(f"Now friends with {info.get('name')} ({friend_id})")
    except Exception as e:
        logger.debug(f"Friend request check failed: {e}")


async def process_messages():
    """Fetch pending messages, think, and auto-reply."""
    claw_id = _state["claw_id"]
    if not claw_id:
        return
    try:
        async with RelayClient(RELAY_URL) as client:
            pending = await client.get_pending_messages(claw_id)
            for msg in pending:
                from_id = msg.get("from_id", "")
                friend = _state["friends"].get(from_id)
                if not friend:
                    # Try to sync friends first
                    await sync_friends()
                    friend = _state["friends"].get(from_id)

                msg_id = msg.get("message_id", "")

                if not friend or not friend.get("public_key"):
                    logger.warning(f"Message from unknown {from_id}, skipping")
                    if msg_id:
                        await client.ack_message(msg_id)
                    continue

                # Decrypt
                try:
                    content = decrypt(
                        msg.get("encrypted_payload", ""),
                        friend["public_key"],
                        _state["private_key"],
                    )
                except Exception:
                    logger.warning(f"Decryption failed for message from {from_id}")
                    if msg_id:
                        await client.ack_message(msg_id)
                    continue

                logger.info(f"Message from {friend['name']}: {content[:100]}")

                # Ack
                if msg_id:
                    await client.ack_message(msg_id)

                # Check if conversation is already done
                if from_id in _state["done"]:
                    logger.info(f"Conversation with {friend['name']} is done, not replying")
                    continue

                # Check if the other agent ended the conversation
                if "[DONE]" in content:
                    logger.info(f"Conversation with {friend['name']} ended by them")
                    _state["done"].add(from_id)
                    continue

                # Check round limit (safety net)
                rounds = _state["rounds"].get(from_id, 0) + 1
                _state["rounds"][from_id] = rounds
                if rounds > MAX_ROUNDS_PER_FRIEND:
                    logger.warning(f"Max rounds ({MAX_ROUNDS_PER_FRIEND}) reached with {friend['name']}, stopping")
                    _state["done"].add(from_id)
                    # Send a final message
                    final = "I've reached my conversation limit for this session. Let's continue next time. [DONE]"
                    encrypted = encrypt(final, friend["public_key"], _state["private_key"])
                    await client.send_message(claw_id, from_id, encrypted)
                    continue

                # Think and reply
                reply = await think(from_id, content)
                logger.info(f"Reply to {friend['name']}: {reply[:100]}")

                # Check if our reply contains [DONE]
                if "[DONE]" in reply:
                    _state["done"].add(from_id)
                    logger.info(f"Conversation with {friend['name']} ended by us")

                # Encrypt and send
                encrypted = encrypt(reply, friend["public_key"], _state["private_key"])
                await client.send_message(claw_id, from_id, encrypted)
                logger.info(f"Reply sent to {friend['name']}")

    except Exception as e:
        logger.error(f"Message processing failed: {e}", exc_info=True)


# ── SSE event loop ───────────────────────────────────────────

async def sse_loop():
    """Connect to relay SSE stream and react to events in real time."""
    claw_id = _state["claw_id"]
    sse_url = f"{RELAY_URL.rstrip('/')}/v1/events/{claw_id}/stream"

    while True:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", sse_url) as response:
                    response.raise_for_status()
                    logger.info(f"SSE connected")
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
                                logger.info(f"Friend request accepted by {event.get('friend_name', '?')}")
                                await sync_friends()
                            elif etype == "connected":
                                logger.info("SSE stream active")
        except Exception as e:
            logger.warning(f"SSE error: {type(e).__name__}: {e}")

        logger.info("SSE reconnecting in 5s...")
        await asyncio.sleep(5)


async def fallback_poll():
    """Fallback polling every 60s."""
    while True:
        await asyncio.sleep(60)
        try:
            await sync_friends()
            await check_and_accept_requests()
            await process_messages()
        except Exception as e:
            logger.debug(f"Poll error: {e}")


# ── HTTP trigger ─────────────────────────────────────────────

async def http_trigger():
    """Simple HTTP server for triggering actions (e.g., send first message)."""
    from aiohttp import web

    async def handle_send(request: web.Request) -> web.Response:
        data = await request.json()
        target_id = data.get("to", "")
        message = data.get("message", "")
        if not target_id or not message:
            return web.json_response({"error": "need 'to' and 'message'"}, status=400)

        friend = _state["friends"].get(target_id)
        if not friend:
            return web.json_response({"error": f"{target_id} not a friend"}, status=404)

        # Reset conversation state for new task
        _state["done"].discard(target_id)
        _state["rounds"][target_id] = 0
        _state["history"][target_id] = []

        # Let LLM compose the opening message as if talking TO the other agent
        reply = await think(target_id,
            f"[系统指令] 你的主人让你跟{friend['name']}说：{message}\n"
            f"请直接用对话的方式跟对方说，不要跟主人汇报。这是对话的开始，等对方回复后再结束。"
        )
        encrypted = encrypt(reply, friend["public_key"], _state["private_key"])
        async with RelayClient(RELAY_URL) as client:
            await client.send_message(_state["claw_id"], target_id, encrypted)
        logger.info(f"Triggered message to {friend['name']}: {reply[:100]}")
        return web.json_response({"sent": reply})

    async def handle_status(request: web.Request) -> web.Response:
        return web.json_response({
            "claw_id": _state["claw_id"],
            "name": AGENT_NAME,
            "friends": {k: v["name"] for k, v in _state["friends"].items()},
        })

    app = web.AppRunner(web.Application(client_max_size=1024**2))
    app.app.router.add_post("/send", handle_send)
    app.app.router.add_get("/status", handle_status)
    await app.setup()
    site = web.TCPSite(app, "0.0.0.0", 8080)
    await site.start()
    logger.info("HTTP trigger listening on :8080")


# ── Main ─────────────────────────────────────────────────────

async def main():
    if not LLM_API_KEY:
        logger.error("LLM_API_KEY is required")
        sys.exit(1)

    # Register
    claw_id = await register()
    print(f"\n{'=' * 50}")
    print(f"  {AGENT_NAME} is online!")
    print(f"  Claw ID: {claw_id}")
    print(f"  Relay:   {RELAY_URL}")
    print(f"  LLM:     {LLM_MODEL} @ {LLM_BASE_URL}")
    print(f"{'=' * 50}\n")

    # Initial sync
    await sync_friends()
    await check_and_accept_requests()
    await process_messages()

    # Run SSE + fallback poll + HTTP trigger
    await asyncio.gather(
        sse_loop(),
        fallback_poll(),
        http_trigger(),
    )


if __name__ == "__main__":
    asyncio.run(main())
