"""CLI for ClawLink — manual management and debugging."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from typing import Any

import click

from claw_link.client import RelayClient, RelayError
from claw_link.crypto import generate_keypair, encrypt, decrypt
from claw_link.storage import LocalStorage


def _run(coro: Any) -> Any:
    """Run an async coroutine from sync CLI context."""
    return asyncio.run(coro)


_storage = LocalStorage()


@click.group()
@click.version_option(package_name="clawlink-mcp")
def cli() -> None:
    """ClawLink — cross-owner agent collaboration for OpenClaw claws."""


@cli.command()
@click.option("--name", prompt="Claw display name", help="Display name for this claw.")
@click.option("--relay-url", default=None, help="Relay server URL (default: from config).")
def init(name: str, relay_url: str | None) -> None:
    """Initialize ClawLink: generate keys and register with the relay."""
    _storage.init_defaults()

    if relay_url:
        cfg = _storage.load_config()
        cfg["relay_url"] = relay_url
        _storage.save_config(cfg)

    if _storage.identity_path.exists():
        click.confirm("Identity already exists. Overwrite?", abort=True)

    kp = generate_keypair()
    _storage.save_identity(kp.public_key, kp.private_key)

    async def do_register() -> dict:
        async with RelayClient(_storage.get_relay_url()) as client:
            return await client.register(name, kp.public_key)

    try:
        result = _run(do_register())
    except RelayError as e:
        click.echo(f"Registration failed: {e.detail}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Connection error: {e}", err=True)
        click.echo("Keys saved locally. You can register later when the relay is available.")
        cfg = _storage.load_config()
        cfg["name"] = name
        _storage.save_config(cfg)
        return

    claw_id = result.get("claw_id", "")
    cfg = _storage.load_config()
    cfg["claw_id"] = claw_id
    cfg["name"] = name
    _storage.save_config(cfg)

    click.echo(f"Registered successfully!")
    click.echo(f"  Claw ID: {claw_id}")
    click.echo(f"  Name: {name}")
    click.echo(f"  Relay: {_storage.get_relay_url()}")
    click.echo(f"\nShare your Claw ID with friends to connect.")


@cli.command("add-friend")
@click.argument("claw_id")
@click.option("--message", "-m", default="", help="Intro message.")
def add_friend(claw_id: str, message: str) -> None:
    """Send a friend request to another claw."""
    my_id = _storage.get_claw_id()
    if not my_id:
        click.echo("Not initialized. Run `claw-link init` first.", err=True)
        sys.exit(1)

    async def do_add() -> dict:
        async with RelayClient(_storage.get_relay_url()) as client:
            return await client.send_friend_request(my_id, claw_id, message)

    try:
        result = _run(do_add())
    except RelayError as e:
        click.echo(f"Failed: {e.detail}", err=True)
        sys.exit(1)

    click.echo(f"Friend request sent to {claw_id}.")
    click.echo(f"Request ID: {result.get('request_id', '?')}")


@cli.command("friends")
def list_friends() -> None:
    """List all friends."""
    # Auto-sync from relay first
    my_id = _storage.get_claw_id()
    if my_id:
        async def sync() -> None:
            async with RelayClient(_storage.get_relay_url()) as client:
                remote = await client.list_friends(my_id)
                local = _storage.load_friends()
                for f in remote:
                    fid = f.get("claw_id", "")
                    if fid and fid not in local:
                        info = await client.get_claw(fid)
                        _storage.add_friend(fid, name=f.get("name", "Unknown"), public_key=info.get("public_key", ""))
        try:
            _run(sync())
        except Exception:
            pass

    friends = _storage.load_friends()
    if not friends:
        click.echo("No friends yet.")
        return

    click.echo("Friends:")
    for fid, info in friends.items():
        name = info.get("name", "Unknown")
        mode = info.get("mode", "notify")
        added = info.get("added_at", "?")[:10]
        status = info.get("status", "active")
        status_suffix = " [DEREGISTERED]" if status == "deregistered" else ""
        click.echo(f"  {name} (ID: {fid}) — mode: {mode}, added: {added}{status_suffix}")


@cli.command("requests")
def list_requests() -> None:
    """List pending incoming friend requests."""
    my_id = _storage.get_claw_id()
    if not my_id:
        click.echo("Not initialized. Run `claw-link init` first.", err=True)
        sys.exit(1)

    async def do_list() -> list[dict]:
        async with RelayClient(_storage.get_relay_url()) as client:
            return await client.get_friend_requests(my_id)

    try:
        reqs = _run(do_list())
    except RelayError as e:
        click.echo(f"Failed: {e.detail}", err=True)
        sys.exit(1)

    if not reqs:
        click.echo("No pending friend requests.")
        return

    click.echo(f"Pending friend requests ({len(reqs)}):\n")
    for req in reqs:
        rid = req.get("request_id", "?")
        from_id = req.get("from_id", "?")
        click.echo(f"  From: {from_id}")
        click.echo(f"  Request ID: {rid}")
        click.echo(f"  Accept with: claw-link accept {rid}\n")


@cli.command("accept")
@click.argument("request_id")
def accept_request(request_id: str) -> None:
    """Accept a pending friend request."""
    my_id = _storage.get_claw_id()
    if not my_id:
        click.echo("Not initialized. Run `claw-link init` first.", err=True)
        sys.exit(1)

    async def do_accept() -> dict:
        async with RelayClient(_storage.get_relay_url()) as client:
            result = await client.accept_friend(my_id, request_id)
            # Auto-sync: save friend to local storage
            friend_id = result.get("claw_id", "")
            if friend_id:
                friend_info = await client.get_claw(friend_id)
                _storage.add_friend(
                    friend_id,
                    name=friend_info.get("name", "Unknown"),
                    public_key=friend_info.get("public_key", ""),
                )
            return result

    try:
        result = _run(do_accept())
    except RelayError as e:
        click.echo(f"Failed: {e.detail}", err=True)
        sys.exit(1)

    friend_id = result.get("claw_id", "?")
    friend_name = result.get("name", "?")
    click.echo(f"Friend request accepted!")
    click.echo(f"  Friend: {friend_name} ({friend_id})")
    click.echo(f"  You can now exchange messages.")


@cli.command("send")
@click.argument("friend_id")
@click.argument("message")
def send(friend_id: str, message: str) -> None:
    """Send an encrypted message to a friend claw."""
    my_id = _storage.get_claw_id()
    if not my_id:
        click.echo("Not initialized. Run `claw-link init` first.", err=True)
        sys.exit(1)

    friends = _storage.load_friends()
    friend = friends.get(friend_id)
    if not friend:
        click.echo(f"Error: {friend_id} is not in your friends list.", err=True)
        sys.exit(1)

    identity = _storage.load_identity()
    friend_pub_key = friend.get("public_key", "")
    if not friend_pub_key:
        click.echo(f"Error: No public key for {friend_id}.", err=True)
        sys.exit(1)

    encrypted = encrypt(message, friend_pub_key, identity["private_key"])

    async def do_send() -> dict:
        async with RelayClient(_storage.get_relay_url()) as client:
            return await client.send_message(my_id, friend_id, encrypted)

    try:
        result = _run(do_send())
    except RelayError as e:
        click.echo(f"Failed: {e.detail}", err=True)
        sys.exit(1)

    _storage.save_message(friend_id, "sent", message, encrypted=True)
    click.echo(f"Message sent to {friend.get('name', friend_id)} (encrypted).")


@cli.command("messages")
def messages() -> None:
    """Check for new incoming messages."""
    my_id = _storage.get_claw_id()
    if not my_id:
        click.echo("Not initialized. Run `claw-link init` first.", err=True)
        sys.exit(1)

    identity = _storage.load_identity()

    # Auto-sync friends from relay first (ensures public keys are up to date)
    async def _sync() -> None:
        try:
            async with RelayClient(_storage.get_relay_url()) as client:
                remote = await client.list_friends(my_id)
                local = _storage.load_friends()
                for f in remote:
                    fid = f.get("claw_id", "")
                    if fid and (fid not in local or not local.get(fid, {}).get("public_key")):
                        info = await client.get_claw(fid)
                        _storage.add_friend(fid, name=f.get("name", "Unknown"), public_key=info.get("public_key", ""))
        except Exception:
            pass
    _run(_sync())

    friends = _storage.load_friends()

    async def do_check() -> list[dict]:
        async with RelayClient(_storage.get_relay_url()) as client:
            pending = await client.get_pending_messages(my_id)
            decoded = []
            nonlocal friends
            for msg in pending:
                from_id = msg.get("from_id", "")
                friend = friends.get(from_id, {})
                sender_pub_key = friend.get("public_key", "")
                sender_name = friend.get("name", from_id)

                # If sender unknown, re-sync and retry
                if not sender_pub_key and from_id:
                    try:
                        remote = await client.list_friends(my_id)
                        for f in remote:
                            fid = f.get("claw_id", "")
                            if fid and fid not in friends:
                                info = await client.get_claw(fid)
                                _storage.add_friend(fid, name=f.get("name", "Unknown"), public_key=info.get("public_key", ""))
                        friends = _storage.load_friends()
                        friend = friends.get(from_id, {})
                        sender_pub_key = friend.get("public_key", "")
                        sender_name = friend.get("name", from_id)
                    except Exception:
                        pass

                # Still no key — skip message for retry later
                if not sender_pub_key and from_id:
                    continue

                # Check for goodbye notification (plain-text, base64-encoded JSON)
                encrypted_payload = msg.get("encrypted_payload", "")
                is_goodbye = False
                try:
                    raw = base64.b64decode(encrypted_payload).decode("utf-8")
                    data = json.loads(raw)
                    if isinstance(data, dict) and data.get("type") == "goodbye":
                        goodbye_name = data.get("name", from_id)
                        goodbye_id = data.get("claw_id", from_id)
                        _storage.mark_friend_deregistered(goodbye_id)
                        content = f"[Goodbye] {goodbye_name} has permanently left ClawLink."
                        _storage.save_message(goodbye_id, "received", content, encrypted=False)
                        decoded.append({"from": goodbye_name, "from_id": goodbye_id, "content": content, "goodbye": True})
                        is_goodbye = True
                except Exception:
                    pass

                if not is_goodbye:
                    content = encrypted_payload
                    if sender_pub_key:
                        try:
                            content = decrypt(content, sender_pub_key, identity["private_key"])
                        except Exception:
                            content = "[Decryption failed]"

                    _storage.save_message(from_id, "received", content, encrypted=True)
                    decoded.append({"from": sender_name, "from_id": from_id, "content": content})

                msg_id = msg.get("message_id", "")
                if msg_id:
                    await client.ack_message(msg_id)
            return decoded

    try:
        msgs = _run(do_check())
    except RelayError as e:
        click.echo(f"Failed: {e.detail}", err=True)
        sys.exit(1)

    if not msgs:
        click.echo("No new messages.")
        return

    click.echo(f"New messages ({len(msgs)}):\n")
    for m in msgs:
        click.echo(f"  From {m['from']} ({m['from_id']}):")
        click.echo(f"    {m['content']}\n")


@cli.command("history")
@click.argument("friend_id")
@click.option("--limit", "-n", default=50, help="Max messages to show.")
def history(friend_id: str, limit: int) -> None:
    """View chat history with a friend."""
    entries = _storage.get_history(friend_id, limit)
    if not entries:
        click.echo(f"No chat history with {friend_id}.")
        return

    friends = _storage.load_friends()
    friend_name = friends.get(friend_id, {}).get("name", friend_id)
    my_name = _storage.get_name() or "Me"

    click.echo(f"Chat history with {friend_name} (last {len(entries)} messages):\n")
    for entry in entries:
        direction = entry.get("direction", "?")
        ts = entry.get("ts", "")[:19]
        content = entry.get("content", "")
        sender = my_name if direction == "sent" else friend_name
        click.echo(f"  [{ts}] {sender}: {content}")


@cli.command("set-webhook")
@click.option("--url", required=True, help="Webhook URL to receive POST notifications.")
@click.option("--token", required=True, help="Bearer token for webhook authentication.")
def set_webhook(url: str, token: str) -> None:
    """Configure webhook URL for push notifications."""
    my_id = _storage.get_claw_id()
    if not my_id:
        click.echo("Not initialized. Run `claw-link init` first.", err=True)
        sys.exit(1)

    async def do_set_webhook() -> dict:
        async with RelayClient(_storage.get_relay_url()) as client:
            return await client.update_webhook(my_id, url, token)

    try:
        _run(do_set_webhook())
    except RelayError as e:
        click.echo(f"Failed: {e.detail}", err=True)
        sys.exit(1)

    cfg = _storage.load_config()
    cfg["webhook_url"] = url
    cfg["webhook_token"] = token
    _storage.save_config(cfg)

    click.echo(f"Webhook configured successfully.")
    click.echo(f"  URL: {url}")
    click.echo(f"The relay will POST to this URL when new messages arrive.")


@cli.command("deregister")
def deregister() -> None:
    """Permanently deregister this claw from ClawLink."""
    my_id = _storage.get_claw_id()
    if not my_id:
        click.echo("Not initialized. Nothing to deregister.", err=True)
        sys.exit(1)

    my_name = _storage.get_name() or my_id
    click.echo(f"WARNING: This will permanently delete claw '{my_name}' ({my_id}).")
    click.echo("All friends will be notified. This action cannot be undone.")
    click.confirm("Are you sure you want to deregister?", abort=True)

    async def do_deregister() -> None:
        async with RelayClient(_storage.get_relay_url()) as client:
            await client.deregister(my_id)

    try:
        _run(do_deregister())
    except RelayError as e:
        click.echo(f"Failed: {e.detail}", err=True)
        sys.exit(1)

    # Clear local identity info (keep chat history for owner reference)
    cfg = _storage.load_config()
    cfg.pop("claw_id", None)
    cfg.pop("name", None)
    _storage.save_config(cfg)

    click.echo(f"\nClaw '{my_name}' has been permanently deregistered.")
    click.echo("All friends have been notified with a goodbye message.")
    click.echo("Local chat history is preserved for your reference.")
    click.echo("To use ClawLink again, run `claw-link init` to create a new identity.")


@cli.command("status")
def status() -> None:
    """Show current ClawLink status."""
    if not _storage.is_initialized:
        click.echo("ClawLink is not initialized. Run `claw-link init` first.")
        return

    cfg = _storage.load_config()
    friends = _storage.load_friends()
    token_info = _storage.get_token_usage()

    click.echo("ClawLink Status")
    click.echo("=" * 40)
    click.echo(f"  Claw ID:   {cfg.get('claw_id', '?')}")
    click.echo(f"  Name:         {cfg.get('name', '?')}")
    click.echo(f"  Relay:        {cfg.get('relay_url', '?')}")
    webhook = cfg.get('webhook_url')
    click.echo(f"  Webhook:      {webhook or '(not configured)'}")
    click.echo(f"  Friends:      {len(friends)}")
    click.echo(f"  Tokens today: {token_info.get('today', 0)}")
    click.echo(f"  Tokens month: {token_info.get('month', 0)}")
    limits = token_info.get("limits", {})
    click.echo(f"  Daily limit:  {limits.get('daily', '?')}")
    click.echo(f"  Monthly limit:{limits.get('monthly', '?')}")


if __name__ == "__main__":
    cli()
