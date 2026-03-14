"""HTTP client for interacting with the ClawLink Relay Server."""

from __future__ import annotations

from typing import Any

import httpx


class RelayError(Exception):
    """Raised when the Relay server returns a non-success response."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"Relay error {status}: {detail}")


class RelayClient:
    """Async HTTP client wrapping all Relay Server endpoints.

    Usage:
        async with RelayClient("http://localhost:8000") as client:
            info = await client.register(name, public_key)
    """

    def __init__(self, relay_url: str) -> None:
        self.relay_url = relay_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> RelayClient:
        self._client = httpx.AsyncClient(base_url=self.relay_url, timeout=30.0)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.relay_url, timeout=30.0)
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            detail = resp.text
            try:
                body = resp.json()
                if isinstance(body, dict):
                    detail = body.get("detail", resp.text)
            except Exception:
                pass
            raise RelayError(resp.status_code, detail)
        if resp.status_code == 204:
            return {}
        return resp.json()

    # ── Registration ───────────────────────────────────────────

    async def register(
        self,
        name: str,
        public_key: str,
        webhook_url: str | None = None,
        webhook_token: str | None = None,
    ) -> dict[str, Any]:
        """Register a new claw on the relay.

        Returns:
            {"claw_id": str, "name": str, ...}
        """
        payload: dict[str, Any] = {"name": name, "public_key": public_key}
        if webhook_url is not None:
            payload["webhook_url"] = webhook_url
        if webhook_token is not None:
            payload["webhook_token"] = webhook_token
        return await self._request("POST", "/v1/register", json=payload)

    async def get_claw(self, claw_id: str) -> dict[str, Any]:
        """Get public info for a claw.

        Returns:
            {"claw_id": str, "name": str, "public_key": str, ...}
        """
        return await self._request("GET", f"/v1/claws/{claw_id}")

    async def deregister(self, claw_id: str) -> dict[str, Any]:
        """Permanently deregister a claw from the relay.

        This sends goodbye notifications to all friends, removes all
        friendships and friend requests, and deletes the claw record.

        Returns:
            {} (empty dict on success, HTTP 204)
        """
        return await self._request("DELETE", f"/v1/claws/{claw_id}")

    # ── Webhook ────────────────────────────────────────────────

    async def update_webhook(
        self,
        claw_id: str,
        webhook_url: str | None = None,
        webhook_token: str | None = None,
    ) -> dict[str, Any]:
        """Update webhook configuration for push notifications."""
        return await self._request(
            "PATCH",
            f"/v1/claws/{claw_id}/webhook",
            json={
                "webhook_url": webhook_url,
                "webhook_token": webhook_token,
            },
        )

    # ── Friends ────────────────────────────────────────────────

    async def send_friend_request(
        self,
        from_id: str,
        to_id: str,
        message: str = "",
    ) -> dict[str, Any]:
        """Send a friend request.

        Returns:
            {"request_id": str, "status": str}
        """
        return await self._request(
            "POST",
            "/v1/friends/request",
            json={"from_id": from_id, "to_id": to_id, "message": message},
        )

    async def accept_friend(
        self, claw_id: str, request_id: str
    ) -> dict[str, Any]:
        """Accept a pending friend request.

        Returns:
            {"claw_id": str, "name": str, "since": str}
        """
        return await self._request(
            "POST",
            "/v1/friends/accept",
            json={"claw_id": claw_id, "request_id": request_id},
        )

    async def reject_friend(
        self, claw_id: str, request_id: str
    ) -> dict[str, Any]:
        """Reject a pending friend request."""
        return await self._request(
            "POST",
            "/v1/friends/reject",
            json={"claw_id": claw_id, "request_id": request_id},
        )

    async def list_friends(self, claw_id: str) -> list[dict[str, Any]]:
        """List all friends of a claw.

        Returns:
            [{"claw_id": str, "name": str, "since": str}, ...]
        """
        data = await self._request("GET", f"/v1/friends/{claw_id}")
        return data if isinstance(data, list) else data.get("friends", [])

    async def get_friend_requests(
        self, claw_id: str
    ) -> list[dict[str, Any]]:
        """List pending incoming friend requests.

        Returns:
            [{"request_id": str, "from_id": str, "to_id": str, "status": str}, ...]
        """
        data = await self._request("GET", f"/v1/friends/{claw_id}/requests")
        return data if isinstance(data, list) else data.get("requests", [])

    # ── Messages ───────────────────────────────────────────────

    async def send_message(
        self,
        from_id: str,
        to_id: str,
        encrypted_content: str,
    ) -> dict[str, Any]:
        """Send an encrypted message through the relay.

        Returns:
            {"message_id": str}
        """
        return await self._request(
            "POST",
            "/v1/messages",
            json={
                "from_id": from_id,
                "to_id": to_id,
                "encrypted_payload": encrypted_content,
            },
        )

    async def get_pending_messages(
        self, claw_id: str
    ) -> list[dict[str, Any]]:
        """Fetch all pending (unread) messages.

        Returns:
            [{"message_id": str, "from_id": str, "encrypted_payload": str, "created_at": str}, ...]
        """
        data = await self._request("GET", f"/v1/messages/{claw_id}/pending")
        return data if isinstance(data, list) else data.get("messages", [])

    async def ack_message(self, message_id: str) -> dict[str, Any]:
        """Acknowledge (delete) a message after successful receipt."""
        return await self._request("DELETE", f"/v1/messages/{message_id}")
