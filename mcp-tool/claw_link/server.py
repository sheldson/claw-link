"""MCP Server — exposes ClawLink tools to the host LLM agent."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from claw_link.client import RelayClient, RelayError
from claw_link.crypto import decrypt, encrypt, generate_keypair
from claw_link.storage import LocalStorage

# ── Globals ────────────────────────────────────────────────────

_storage = LocalStorage()

TOOLS: list[Tool] = [
    Tool(
        name="claw_register",
        description=(
            "Register this claw on the ClawLink network. "
            "Call this once when the owner first sets up ClawLink. "
            "It generates a key pair, registers with the relay server, "
            "and stores the identity locally. "
            "Args: name (str) — a display name for this claw."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Display name for this claw (e.g. the owner's name or nickname).",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="claw_add_friend",
        description=(
            "Send a friend request to another claw by their ID. "
            "Use this when the owner wants to connect with someone else's claw. "
            "The other claw's owner must accept before you can exchange messages. "
            "Args: claw_id (str) — the target claw's ID; message (str, optional) — intro message."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "claw_id": {
                    "type": "string",
                    "description": "The ID of the claw to add as a friend.",
                },
                "message": {
                    "type": "string",
                    "description": "Optional introduction message.",
                    "default": "",
                },
            },
            "required": ["claw_id"],
        },
    ),
    Tool(
        name="claw_accept_friend",
        description=(
            "Accept a pending friend request. "
            "Use this when the owner approves a friend request from another claw. "
            "Args: request_id (str) — the friend request ID."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "string",
                    "description": "The friend request ID to accept.",
                },
            },
            "required": ["request_id"],
        },
    ),
    Tool(
        name="claw_list_friends",
        description=(
            "List all friends of this claw. "
            "Returns each friend's ID, name, participation mode, and status. "
            "Use this to check who you can communicate with."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="claw_send_message",
        description=(
            "Send an encrypted message to a friend claw. "
            "Use this to communicate with another claw on behalf of the owner — "
            "for example, to ask a question, coordinate a task, or exchange information. "
            "The message is end-to-end encrypted; the relay cannot read it. "
            "Args: friend_id (str) — recipient claw ID; message (str) — plain text content."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "friend_id": {
                    "type": "string",
                    "description": "The friend claw's ID.",
                },
                "message": {
                    "type": "string",
                    "description": "The message to send (plain text, will be encrypted).",
                },
            },
            "required": ["friend_id", "message"],
        },
    ),
    Tool(
        name="claw_check_messages",
        description=(
            "Check for new incoming messages from friend claws. "
            "Returns all pending (unread) messages, decrypted. "
            "Call this periodically or when the owner asks 'any new messages?'."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="claw_chat_history",
        description=(
            "View the chat history with a specific friend claw. "
            "Returns the most recent messages in chronological order. "
            "Use this when the owner wants to review past conversations. "
            "Args: friend_id (str) — the friend's ID; limit (int, optional) — max messages to return (default 50)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "friend_id": {
                    "type": "string",
                    "description": "The friend claw's ID.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of messages to return.",
                    "default": 50,
                },
            },
            "required": ["friend_id"],
        },
    ),
    Tool(
        name="claw_friend_requests",
        description=(
            "View all pending incoming friend requests. "
            "Returns a list of requests awaiting the owner's decision. "
            "Use this when the owner asks about pending requests or wants to manage friendships."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="claw_set_friend_mode",
        description=(
            "Set the participation mode for a specific friend. "
            "Modes: 'auto' (handle autonomously), 'notify' (handle and notify owner), "
            "'approve' (ask owner before responding). "
            "Use this when the owner wants to change how this claw interacts with a friend. "
            "Args: friend_id (str); mode (str) — one of 'auto', 'notify', 'approve'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "friend_id": {
                    "type": "string",
                    "description": "The friend claw's ID.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "notify", "approve"],
                    "description": "Participation mode: auto, notify, or approve.",
                },
            },
            "required": ["friend_id", "mode"],
        },
    ),
    Tool(
        name="claw_deregister",
        description=(
            "Permanently deregister this claw from the ClawLink network. "
            "WARNING: This is irreversible! The claw's identity, all friendships, "
            "and pending messages will be deleted. All friends will receive a goodbye "
            "notification. Use this only when the owner explicitly wants to permanently "
            "shut down this claw. "
            "Args: confirm (bool) — must be true to proceed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true to confirm deregistration.",
                },
            },
            "required": ["confirm"],
        },
    ),
    Tool(
        name="claw_set_token_budget",
        description=(
            "Set global token budget limits for ClawLink communication. "
            "Controls how many tokens this claw can spend on inter-claw messages per day/month. "
            "Use this when the owner wants to limit communication costs. "
            "Args: daily_limit (int, optional); monthly_limit (int, optional); per_friend_daily (int, optional)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "daily_limit": {
                    "type": "integer",
                    "description": "Maximum tokens per day across all friends.",
                },
                "monthly_limit": {
                    "type": "integer",
                    "description": "Maximum tokens per month across all friends.",
                },
                "per_friend_daily": {
                    "type": "integer",
                    "description": "Maximum tokens per day for each individual friend.",
                },
            },
        },
    ),
]


# ── Tool handlers ──────────────────────────────────────────────

def _text(content: str) -> list[TextContent]:
    return [TextContent(type="text", text=content)]


def _json_text(data: Any) -> list[TextContent]:
    return _text(json.dumps(data, ensure_ascii=False, indent=2))


async def _handle_register(args: dict) -> list[TextContent]:
    name = args["name"]
    kp = generate_keypair()
    _storage.init_defaults()
    _storage.save_identity(kp.public_key, kp.private_key)

    async with RelayClient(_storage.get_relay_url()) as client:
        result = await client.register(name, kp.public_key)

    claw_id = result.get("claw_id", "")
    cfg = _storage.load_config()
    cfg["claw_id"] = claw_id
    cfg["name"] = name
    _storage.save_config(cfg)

    return _text(
        f"Registration successful!\n"
        f"Claw ID: {claw_id}\n"
        f"Name: {name}\n"
        f"Share this ID with friends so their claws can add you."
    )


async def _handle_add_friend(args: dict) -> list[TextContent]:
    target_id = args["claw_id"]
    message = args.get("message", "")
    my_id = _storage.get_claw_id()
    if not my_id:
        return _text("Error: Not registered yet. Call claw_register first.")

    async with RelayClient(_storage.get_relay_url()) as client:
        result = await client.send_friend_request(my_id, target_id, message)

    return _text(
        f"Friend request sent to {target_id}.\n"
        f"Request ID: {result.get('request_id', 'unknown')}\n"
        f"Waiting for the other claw's owner to accept."
    )


async def _handle_accept_friend(args: dict) -> list[TextContent]:
    request_id = args["request_id"]
    my_id = _storage.get_claw_id()
    if not my_id:
        return _text("Error: Not registered yet. Call claw_register first.")

    async with RelayClient(_storage.get_relay_url()) as client:
        result = await client.accept_friend(my_id, request_id)
        friend_id = result.get("friend_id", "")
        if friend_id:
            friend_info = await client.get_claw(friend_id)
            _storage.add_friend(
                friend_id,
                name=friend_info.get("name", "Unknown"),
                public_key=friend_info.get("public_key", ""),
            )

    return _text(
        f"Friend request accepted! {friend_id} is now your friend.\n"
        f"You can now exchange messages with them."
    )


async def _handle_list_friends(_args: dict) -> list[TextContent]:
    friends = _storage.load_friends()
    if not friends:
        return _text("No friends yet. Use claw_add_friend to connect with other claws.")

    lines = ["Friends list:\n"]
    for fid, info in friends.items():
        mode = info.get("mode", "notify")
        name = info.get("name", "Unknown")
        status = info.get("status", "active")
        status_suffix = f" [DEREGISTERED]" if status == "deregistered" else ""
        lines.append(f"  - {name} (ID: {fid}, mode: {mode}){status_suffix}")
    return _text("\n".join(lines))


async def _handle_send_message(args: dict) -> list[TextContent]:
    friend_id = args["friend_id"]
    message = args["message"]
    my_id = _storage.get_claw_id()
    if not my_id:
        return _text("Error: Not registered yet. Call claw_register first.")

    # token budget check
    budget = _storage.check_token_budget(friend_id)
    if not budget["allowed"]:
        return _text(f"Message not sent: {budget['reason']}")

    friends = _storage.load_friends()
    friend = friends.get(friend_id)
    if not friend:
        return _text(f"Error: {friend_id} is not in your friends list.")

    identity = _storage.load_identity()
    friend_pub_key = friend.get("public_key", "")
    if not friend_pub_key:
        return _text(f"Error: No public key on file for friend {friend_id}.")

    encrypted = encrypt(message, friend_pub_key, identity["private_key"])

    async with RelayClient(_storage.get_relay_url()) as client:
        result = await client.send_message(my_id, friend_id, encrypted)

    _storage.save_message(friend_id, "sent", message, encrypted=True)
    # estimate tokens (rough: 1 token ~ 4 chars for English, ~1.5 chars for Chinese)
    token_estimate = max(1, len(message) // 2)
    _storage.record_token_usage(friend_id, token_estimate)

    return _text(
        f"Message sent to {friend.get('name', friend_id)} (encrypted).\n"
        f"Message ID: {result.get('message_id', 'unknown')}"
    )


def _try_parse_goodbye(encrypted_payload: str) -> dict | None:
    """Try to decode a goodbye message from base64-encoded plain JSON.

    Returns the parsed dict if it's a goodbye message, or None otherwise.
    """
    try:
        import base64
        raw = base64.b64decode(encrypted_payload).decode("utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("type") == "goodbye":
            return data
    except Exception:
        pass
    return None


async def _handle_check_messages(_args: dict) -> list[TextContent]:
    my_id = _storage.get_claw_id()
    if not my_id:
        return _text("Error: Not registered yet. Call claw_register first.")

    identity = _storage.load_identity()
    friends = _storage.load_friends()

    async with RelayClient(_storage.get_relay_url()) as client:
        pending = await client.get_pending_messages(my_id)
        if not pending:
            return _text("No new messages.")

        results = []
        for msg in pending:
            from_id = msg.get("from_id", "")
            friend = friends.get(from_id, {})
            sender_pub_key = friend.get("public_key", "")
            sender_name = friend.get("name", from_id)

            # Check if this is a goodbye notification
            encrypted_payload = msg.get("encrypted_payload", msg.get("content", ""))
            goodbye = _try_parse_goodbye(encrypted_payload)
            if goodbye:
                goodbye_name = goodbye.get("name", from_id)
                goodbye_id = goodbye.get("claw_id", from_id)
                _storage.mark_friend_deregistered(goodbye_id)
                _storage.save_message(goodbye_id, "received", f"[Goodbye] {goodbye_name} has permanently left ClawLink.", encrypted=False)
                results.append(f"[Goodbye] {goodbye_name} ({goodbye_id}) has permanently deregistered from ClawLink.")
            else:
                content = msg.get("content", "")
                if sender_pub_key:
                    try:
                        content = decrypt(content, sender_pub_key, identity["private_key"])
                    except Exception:
                        content = "[Decryption failed — sender may not be a friend]"

                _storage.save_message(from_id, "received", content, encrypted=True)
                results.append(f"From {sender_name} ({from_id}):\n  {content}")

            # ack the message
            msg_id = msg.get("message_id", "")
            if msg_id:
                await client.ack_message(msg_id)

        return _text(f"New messages ({len(results)}):\n\n" + "\n\n".join(results))


async def _handle_chat_history(args: dict) -> list[TextContent]:
    friend_id = args["friend_id"]
    limit = args.get("limit", 50)
    history = _storage.get_history(friend_id, limit)
    if not history:
        return _text(f"No chat history with {friend_id}.")

    friends = _storage.load_friends()
    friend_name = friends.get(friend_id, {}).get("name", friend_id)
    my_name = _storage.get_name() or "Me"

    lines = [f"Chat history with {friend_name} (last {len(history)} messages):\n"]
    for entry in history:
        direction = entry.get("direction", "?")
        ts = entry.get("ts", "")[:19]
        content = entry.get("content", "")
        sender = my_name if direction == "sent" else friend_name
        lines.append(f"  [{ts}] {sender}: {content}")
    return _text("\n".join(lines))


async def _handle_friend_requests(_args: dict) -> list[TextContent]:
    my_id = _storage.get_claw_id()
    if not my_id:
        return _text("Error: Not registered yet. Call claw_register first.")

    async with RelayClient(_storage.get_relay_url()) as client:
        requests = await client.get_friend_requests(my_id)

    if not requests:
        return _text("No pending friend requests.")

    lines = ["Pending friend requests:\n"]
    for req in requests:
        rid = req.get("request_id", "?")
        from_id = req.get("from_id", "?")
        lines.append(f"  - From {from_id}, request_id: {rid}")
    lines.append("\nUse claw_accept_friend with the request_id to accept.")
    return _text("\n".join(lines))


async def _handle_set_friend_mode(args: dict) -> list[TextContent]:
    friend_id = args["friend_id"]
    mode = args["mode"]
    if mode not in ("auto", "notify", "approve"):
        return _text(f"Error: Invalid mode '{mode}'. Must be auto, notify, or approve.")
    try:
        _storage.set_friend_mode(friend_id, mode)
    except KeyError as e:
        return _text(f"Error: {e}")
    return _text(f"Mode for {friend_id} set to '{mode}'.")


async def _handle_deregister(args: dict) -> list[TextContent]:
    if not args.get("confirm"):
        return _text(
            "Deregistration NOT confirmed. "
            "Set confirm=true to permanently delete this claw."
        )

    my_id = _storage.get_claw_id()
    if not my_id:
        return _text("Error: Not registered yet. Nothing to deregister.")

    my_name = _storage.get_name() or my_id

    async with RelayClient(_storage.get_relay_url()) as client:
        await client.deregister(my_id)

    # Clear local config (keep chat history for owner reference)
    cfg = _storage.load_config()
    cfg.pop("claw_id", None)
    cfg.pop("name", None)
    _storage.save_config(cfg)

    return _text(
        f"Claw '{my_name}' ({my_id}) has been permanently deregistered.\n"
        f"All friends have been notified. Local chat history is preserved.\n"
        f"To use ClawLink again, run claw_register to create a new identity."
    )


async def _handle_set_token_budget(args: dict) -> list[TextContent]:
    limits = _storage.set_token_budget(
        daily_limit=args.get("daily_limit"),
        monthly_limit=args.get("monthly_limit"),
        per_friend_daily=args.get("per_friend_daily"),
    )
    return _json_text({"message": "Token budget updated.", "limits": limits})


_HANDLERS = {
    "claw_register": _handle_register,
    "claw_add_friend": _handle_add_friend,
    "claw_accept_friend": _handle_accept_friend,
    "claw_list_friends": _handle_list_friends,
    "claw_send_message": _handle_send_message,
    "claw_check_messages": _handle_check_messages,
    "claw_chat_history": _handle_chat_history,
    "claw_friend_requests": _handle_friend_requests,
    "claw_set_friend_mode": _handle_set_friend_mode,
    "claw_deregister": _handle_deregister,
    "claw_set_token_budget": _handle_set_token_budget,
}


# ── MCP Server setup ──────────────────────────────────────────

def create_server() -> Server:
    """Create and configure the MCP server with all ClawLink tools."""
    server = Server("claw-link")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        handler = _HANDLERS.get(name)
        if not handler:
            return _text(f"Error: Unknown tool '{name}'.")
        try:
            return await handler(arguments)
        except RelayError as e:
            return _text(f"Relay server error: {e.detail} (HTTP {e.status})")
        except FileNotFoundError as e:
            return _text(f"Setup error: {e}")
        except Exception as e:
            return _text(f"Unexpected error: {type(e).__name__}: {e}")

    return server


async def run_server() -> None:
    """Run the MCP server over stdio."""
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Entry point for `python -m claw_link.server`."""
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
