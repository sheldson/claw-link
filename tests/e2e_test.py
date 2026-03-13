"""
ClawLink End-to-End Acceptance Test

Simulates two (or more) claws interacting through the relay server:
registration, friend requests, encrypted messaging, unfriending, and offline messages.

Usage:
    # With pytest
    pytest tests/e2e_test.py -v

    # Standalone
    python tests/e2e_test.py
"""

from __future__ import annotations

import os
import sys
import signal
import subprocess
import time
import textwrap
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Path setup: make sure we can import crypto from mcp-tool
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_TOOL_DIR = os.path.join(_PROJECT_ROOT, "mcp-tool")
if _MCP_TOOL_DIR not in sys.path:
    sys.path.insert(0, _MCP_TOOL_DIR)

from claw_link.crypto import generate_keypair, encrypt, decrypt  # noqa: E402

import httpx  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RELAY_PORT = 9999
RELAY_URL = f"http://localhost:{RELAY_PORT}"
STARTUP_TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@dataclass
class ClawIdentity:
    """Holds everything a test claw needs."""
    name: str
    claw_id: str
    public_key: str
    private_key: str


class ScenarioResult:
    """Collects pass/fail results across all scenarios."""

    def __init__(self) -> None:
        self.results: list[tuple[int, str, bool, str]] = []

    def record(self, num: int, title: str, passed: bool, detail: str = "") -> None:
        self.results.append((num, title, passed, detail))
        icon = "\u2705" if passed else "\u274c"
        status = "PASS" if passed else f"FAIL: {detail}"
        print(f"{icon} \u573a\u666f {num}\uff1a{title} \u2014 {status}")

    def summary(self) -> int:
        total = len(self.results)
        passed = sum(1 for _, _, p, _ in self.results if p)
        print()
        print("=" * 40)
        print(f"\u9a8c\u6536\u7ed3\u679c\uff1a{passed}/{total} \u901a\u8fc7")
        print("=" * 40)
        return 0 if passed == total else 1


# ---------------------------------------------------------------------------
# Relay server lifecycle
# ---------------------------------------------------------------------------
_relay_proc: subprocess.Popen | None = None


def _start_relay() -> subprocess.Popen:
    """Start the relay server in a subprocess, wait until healthy."""
    env = os.environ.copy()
    env["PORT"] = str(RELAY_PORT)
    # Use an in-memory SQLite database so each run is clean
    env["DATABASE_URL"] = "sqlite+aiosqlite://"

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "relay.main:app",
            "--host", "127.0.0.1",
            "--port", str(RELAY_PORT),
        ],
        cwd=os.path.join(_PROJECT_ROOT, "relay"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for the server to be ready
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        try:
            r = httpx.get(f"{RELAY_URL}/health", timeout=1.0)
            if r.status_code == 200:
                return proc
        except (httpx.ConnectError, httpx.ReadError):
            pass
        time.sleep(0.3)

    # If we get here, startup failed
    proc.kill()
    stdout, stderr = proc.communicate()
    raise RuntimeError(
        f"Relay server failed to start within {STARTUP_TIMEOUT}s.\n"
        f"stdout: {stdout.decode()}\nstderr: {stderr.decode()}"
    )


def _stop_relay(proc: subprocess.Popen) -> None:
    """Gracefully stop the relay server."""
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


# ---------------------------------------------------------------------------
# API helpers (raw httpx, no client wrapper)
# ---------------------------------------------------------------------------
def register_claw(client: httpx.Client, name: str) -> ClawIdentity:
    """Register a claw and return its identity."""
    kp = generate_keypair()
    resp = client.post(
        f"{RELAY_URL}/v1/register",
        json={"name": name, "public_key": kp.public_key},
    )
    assert resp.status_code == 201, f"register failed: {resp.status_code} {resp.text}"
    data = resp.json()
    return ClawIdentity(
        name=name,
        claw_id=data["claw_id"],
        public_key=kp.public_key,
        private_key=kp.private_key,
    )


def send_friend_request(client: httpx.Client, from_id: str, to_id: str) -> dict:
    resp = client.post(
        f"{RELAY_URL}/v1/friends/request",
        json={"from_id": from_id, "to_id": to_id},
    )
    assert resp.status_code == 201, f"friend request failed: {resp.status_code} {resp.text}"
    return resp.json()


def get_pending_requests(client: httpx.Client, claw_id: str) -> list[dict]:
    resp = client.get(f"{RELAY_URL}/v1/friends/{claw_id}/requests")
    assert resp.status_code == 200, f"get pending requests failed: {resp.status_code}"
    return resp.json()


def accept_friend_request(client: httpx.Client, claw_id: str, request_id: str) -> dict:
    resp = client.post(
        f"{RELAY_URL}/v1/friends/accept",
        json={"claw_id": claw_id, "request_id": request_id},
    )
    assert resp.status_code == 200, f"accept failed: {resp.status_code} {resp.text}"
    return resp.json()


def list_friends(client: httpx.Client, claw_id: str) -> list[dict]:
    resp = client.get(f"{RELAY_URL}/v1/friends/{claw_id}")
    assert resp.status_code == 200, f"list friends failed: {resp.status_code}"
    return resp.json()


def send_encrypted_message(
    client: httpx.Client,
    sender: ClawIdentity,
    recipient: ClawIdentity,
    plaintext: str,
) -> dict:
    """Encrypt and send a message. Returns the API response."""
    encrypted_payload = encrypt(plaintext, recipient.public_key, sender.private_key)
    resp = client.post(
        f"{RELAY_URL}/v1/messages",
        json={
            "from_id": sender.claw_id,
            "to_id": recipient.claw_id,
            "encrypted_payload": encrypted_payload,
        },
    )
    assert resp.status_code == 201, f"send message failed: {resp.status_code} {resp.text}"
    return resp.json()


def get_pending_messages(client: httpx.Client, claw_id: str) -> list[dict]:
    resp = client.get(f"{RELAY_URL}/v1/messages/{claw_id}/pending")
    assert resp.status_code == 200, f"get messages failed: {resp.status_code}"
    return resp.json()


def ack_message(client: httpx.Client, message_id: str) -> None:
    resp = client.delete(f"{RELAY_URL}/v1/messages/{message_id}")
    assert resp.status_code == 204, f"ack failed: {resp.status_code}"


def decrypt_message(
    msg: dict,
    sender_public_key: str,
    recipient_private_key: str,
) -> str:
    """Decrypt a message payload returned by the API."""
    return decrypt(msg["encrypted_payload"], sender_public_key, recipient_private_key)


def make_friends(
    client: httpx.Client,
    a: ClawIdentity,
    b: ClawIdentity,
) -> None:
    """Full friend-request + accept flow between a and b."""
    req_data = send_friend_request(client, a.claw_id, b.claw_id)
    request_id = req_data["request_id"]
    accept_friend_request(client, b.claw_id, request_id)


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------
def run_all_scenarios() -> int:
    """Run all 8 scenarios and return exit code (0 = all pass)."""
    results = ScenarioResult()
    proc = _start_relay()

    try:
        with httpx.Client(timeout=10.0) as client:
            # Storage for identities across scenarios
            claw_a: ClawIdentity | None = None
            claw_b: ClawIdentity | None = None

            # ── Scenario 1: Registration ──────────────────────────
            try:
                claw_a = register_claw(client, "Claw Alice")
                assert claw_a.claw_id.startswith("claw_"), \
                    f"unexpected id format: {claw_a.claw_id}"
                claw_b = register_claw(client, "Claw Bob")
                assert claw_b.claw_id.startswith("claw_"), \
                    f"unexpected id format: {claw_b.claw_id}"
                assert claw_a.claw_id != claw_b.claw_id
                results.record(1, "\u9f99\u867e\u6ce8\u518c", True)
            except Exception as e:
                results.record(1, "\u9f99\u867e\u6ce8\u518c", False, str(e))
                return results.summary()  # can't continue without IDs

            # ── Scenario 2: Add Friend ────────────────────────────
            try:
                # A sends friend request to B
                req_data = send_friend_request(client, claw_a.claw_id, claw_b.claw_id)
                request_id = req_data["request_id"]
                assert req_data["status"] == "pending"

                # B sees the pending request
                pending = get_pending_requests(client, claw_b.claw_id)
                assert len(pending) == 1, f"expected 1 pending request, got {len(pending)}"
                assert pending[0]["from_id"] == claw_a.claw_id

                # B accepts
                accept_data = accept_friend_request(client, claw_b.claw_id, request_id)
                assert accept_data["claw_id"] == claw_a.claw_id

                # Both should see each other in friends list
                a_friends = list_friends(client, claw_a.claw_id)
                b_friends = list_friends(client, claw_b.claw_id)
                a_friend_ids = [f["claw_id"] for f in a_friends]
                b_friend_ids = [f["claw_id"] for f in b_friends]
                assert claw_b.claw_id in a_friend_ids, "B not in A's friend list"
                assert claw_a.claw_id in b_friend_ids, "A not in B's friend list"

                results.record(2, "\u52a0\u597d\u53cb", True)
            except Exception as e:
                results.record(2, "\u52a0\u597d\u53cb", False, str(e))

            # ── Scenario 3: Send Encrypted Message ────────────────
            try:
                test_msg = "Hey Bob, want to coordinate on that project?"

                # A encrypts with B's public key and sends
                msg_resp = send_encrypted_message(client, claw_a, claw_b, test_msg)
                msg_id = msg_resp["message_id"]

                # B pulls pending messages
                pending_msgs = get_pending_messages(client, claw_b.claw_id)
                assert len(pending_msgs) == 1, f"expected 1 message, got {len(pending_msgs)}"

                # B decrypts using A's public key
                decrypted = decrypt_message(
                    pending_msgs[0],
                    claw_a.public_key,
                    claw_b.private_key,
                )
                assert decrypted == test_msg, f"decrypted mismatch: {decrypted!r}"

                # B acknowledges (DELETE)
                ack_message(client, pending_msgs[0]["message_id"])

                # No more pending messages
                remaining = get_pending_messages(client, claw_b.claw_id)
                assert len(remaining) == 0, f"expected 0 remaining, got {len(remaining)}"

                results.record(3, "\u53d1\u9001\u52a0\u5bc6\u6d88\u606f", True)
            except Exception as e:
                results.record(3, "\u53d1\u9001\u52a0\u5bc6\u6d88\u606f", False, str(e))

            # ── Scenario 4: Multi-round Conversation ──────────────
            try:
                msg_b_to_a = "Sure! Let me check my owner's calendar."
                send_encrypted_message(client, claw_b, claw_a, msg_b_to_a)

                a_msgs = get_pending_messages(client, claw_a.claw_id)
                assert len(a_msgs) == 1
                decrypted_b = decrypt_message(a_msgs[0], claw_b.public_key, claw_a.private_key)
                assert decrypted_b == msg_b_to_a
                ack_message(client, a_msgs[0]["message_id"])

                msg_a_to_b_2 = "Great, Tuesday 2pm works for us."
                send_encrypted_message(client, claw_a, claw_b, msg_a_to_b_2)

                b_msgs = get_pending_messages(client, claw_b.claw_id)
                assert len(b_msgs) == 1
                decrypted_a2 = decrypt_message(b_msgs[0], claw_a.public_key, claw_b.private_key)
                assert decrypted_a2 == msg_a_to_b_2
                ack_message(client, b_msgs[0]["message_id"])

                results.record(4, "\u591a\u8f6e\u5bf9\u8bdd", True)
            except Exception as e:
                results.record(4, "\u591a\u8f6e\u5bf9\u8bdd", False, str(e))

            # ── Scenario 5: Non-friend Rejected ───────────────────
            try:
                claw_c = register_claw(client, "Claw Charlie")
                # C tries to send A a message without being friends
                encrypted_payload = encrypt(
                    "Hey, I'm not your friend!",
                    claw_a.public_key,
                    claw_c.private_key,
                )
                resp = client.post(
                    f"{RELAY_URL}/v1/messages",
                    json={
                        "from_id": claw_c.claw_id,
                        "to_id": claw_a.claw_id,
                        "encrypted_payload": encrypted_payload,
                    },
                )
                assert resp.status_code == 403, \
                    f"expected 403, got {resp.status_code}"
                results.record(5, "\u975e\u597d\u53cb\u88ab\u62d2\u7edd", True)
            except Exception as e:
                results.record(5, "\u975e\u597d\u53cb\u88ab\u62d2\u7edd", False, str(e))

            # ── Scenario 6: Non-existent ID ───────────────────────
            try:
                resp = client.post(
                    f"{RELAY_URL}/v1/friends/request",
                    json={
                        "from_id": claw_a.claw_id,
                        "to_id": "claw_nonexistent",
                    },
                )
                assert resp.status_code == 404, \
                    f"expected 404, got {resp.status_code}"
                results.record(6, "\u4e0d\u5b58\u5728\u7684 ID", True)
            except Exception as e:
                results.record(6, "\u4e0d\u5b58\u5728\u7684 ID", False, str(e))

            # ── Scenario 7: Unfriend / Block ──────────────────────
            try:
                # A removes B as friend
                resp = client.delete(
                    f"{RELAY_URL}/v1/friends/{claw_a.claw_id}/{claw_b.claw_id}"
                )
                assert resp.status_code == 204, \
                    f"delete friend failed: {resp.status_code}"

                # A tries to send B a message — should be rejected (not friends anymore)
                encrypted_payload = encrypt(
                    "Are you still there?",
                    claw_b.public_key,
                    claw_a.private_key,
                )
                resp = client.post(
                    f"{RELAY_URL}/v1/messages",
                    json={
                        "from_id": claw_a.claw_id,
                        "to_id": claw_b.claw_id,
                        "encrypted_payload": encrypted_payload,
                    },
                )
                assert resp.status_code == 403, \
                    f"expected 403 after unfriend, got {resp.status_code}"
                results.record(7, "\u62c9\u9ed1\u597d\u53cb", True)
            except Exception as e:
                results.record(7, "\u62c9\u9ed1\u597d\u53cb", False, str(e))

            # ── Scenario 8: Offline Messages ──────────────────────
            try:
                claw_d = register_claw(client, "Claw Diana")

                # A and D become friends
                make_friends(client, claw_a, claw_d)

                # A sends 3 messages (D doesn't pull — simulating offline)
                offline_msgs = [
                    "Message 1: Hi Diana!",
                    "Message 2: Are you available Thursday?",
                    "Message 3: Let me know when you're back online.",
                ]
                for msg_text in offline_msgs:
                    send_encrypted_message(client, claw_a, claw_d, msg_text)

                # D finally comes online and pulls all pending messages
                d_msgs = get_pending_messages(client, claw_d.claw_id)
                assert len(d_msgs) == 3, f"expected 3 offline messages, got {len(d_msgs)}"

                # Verify order and content
                for i, (msg, expected_text) in enumerate(zip(d_msgs, offline_msgs)):
                    decrypted_text = decrypt_message(
                        msg, claw_a.public_key, claw_d.private_key
                    )
                    assert decrypted_text == expected_text, \
                        f"message {i+1} mismatch: {decrypted_text!r} != {expected_text!r}"

                results.record(8, "\u79bb\u7ebf\u6d88\u606f", True)
            except Exception as e:
                results.record(8, "\u79bb\u7ebf\u6d88\u606f", False, str(e))

    finally:
        _stop_relay(proc)

    return results.summary()


# ---------------------------------------------------------------------------
# pytest entry point
# ---------------------------------------------------------------------------
def test_e2e_acceptance():
    """Pytest wrapper — runs all scenarios, fails if any scenario fails."""
    exit_code = run_all_scenarios()
    assert exit_code == 0, "Some e2e scenarios failed (see output above)"


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sys.exit(run_all_scenarios())
