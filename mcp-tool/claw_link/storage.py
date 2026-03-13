"""Local storage for claw identity, friends, chat history, and config.

All data lives under ~/.claw-link/:
  config.yaml          — relay URL, claw ID, name
  identity.json        — encrypted key pair
  friends.yaml         — friend list with per-friend settings
  chat_history/<id>.jsonl — one message per line
  social_rules.md      — owner-defined social rules
  token_budget.yaml    — token consumption config and daily/monthly counters
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_BASE_DIR = Path.home() / ".claw-link"
_DEFAULTS_DIR = Path(__file__).parent / "defaults"


class LocalStorage:
    """Manage all local claw data."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base = base_dir or _BASE_DIR
        self.config_path = self.base / "config.yaml"
        self.identity_path = self.base / "identity.json"
        self.friends_path = self.base / "friends.yaml"
        self.history_dir = self.base / "chat_history"
        self.rules_path = self.base / "social_rules.md"
        self.token_path = self.base / "token_budget.yaml"

    # ── bootstrap ──────────────────────────────────────────────

    def ensure_dirs(self) -> None:
        """Create directory structure if missing."""
        self.base.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(exist_ok=True)

    def init_defaults(self) -> None:
        """Copy default config and social rules if they don't exist."""
        self.ensure_dirs()
        if not self.config_path.exists():
            shutil.copy(_DEFAULTS_DIR / "config.yaml", self.config_path)
        if not self.rules_path.exists():
            shutil.copy(_DEFAULTS_DIR / "social_rules.md", self.rules_path)
        if not self.friends_path.exists():
            self.friends_path.write_text(yaml.dump({"friends": {}}, allow_unicode=True))
        if not self.token_path.exists():
            cfg = self.load_config()
            budget = cfg.get("token_budget", {})
            token_data = {
                "limits": {
                    "daily": budget.get("daily_limit", 100000),
                    "monthly": budget.get("monthly_limit", 2000000),
                    "per_friend_daily": budget.get("per_friend_daily", 20000),
                },
                "usage": {},
            }
            self.token_path.write_text(yaml.dump(token_data, allow_unicode=True))

    @property
    def is_initialized(self) -> bool:
        return self.config_path.exists() and self.identity_path.exists()

    # ── config ─────────────────────────────────────────────────

    def load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        return yaml.safe_load(self.config_path.read_text()) or {}

    def save_config(self, cfg: dict[str, Any]) -> None:
        self.ensure_dirs()
        self.config_path.write_text(yaml.dump(cfg, allow_unicode=True, default_flow_style=False))

    def get_relay_url(self) -> str:
        return self.load_config().get("relay_url", "http://localhost:8000")

    def get_claw_id(self) -> str:
        return self.load_config().get("claw_id", "")

    def get_name(self) -> str:
        return self.load_config().get("name", "")

    # ── identity (key pair) ────────────────────────────────────

    def save_identity(self, public_key: str, private_key: str) -> None:
        self.ensure_dirs()
        self.identity_path.write_text(json.dumps({
            "public_key": public_key,
            "private_key": private_key,
        }, indent=2))
        # restrict permissions to owner only
        self.identity_path.chmod(0o600)

    def load_identity(self) -> dict[str, str]:
        """Returns {"public_key": ..., "private_key": ...}."""
        if not self.identity_path.exists():
            raise FileNotFoundError(
                "Identity not found. Run `claw-link init` first."
            )
        return json.loads(self.identity_path.read_text())

    # ── friends ────────────────────────────────────────────────

    def load_friends(self) -> dict[str, dict]:
        """Return {friend_id: {name, public_key, mode, ...}}."""
        if not self.friends_path.exists():
            return {}
        data = yaml.safe_load(self.friends_path.read_text()) or {}
        return data.get("friends", {})

    def save_friends(self, friends: dict[str, dict]) -> None:
        self.ensure_dirs()
        self.friends_path.write_text(
            yaml.dump({"friends": friends}, allow_unicode=True, default_flow_style=False)
        )

    def add_friend(
        self,
        friend_id: str,
        name: str,
        public_key: str,
        mode: str = "notify",
    ) -> None:
        friends = self.load_friends()
        friends[friend_id] = {
            "name": name,
            "public_key": public_key,
            "mode": mode,
            "token_daily_limit": None,  # uses global default
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save_friends(friends)

    def set_friend_mode(self, friend_id: str, mode: str) -> None:
        friends = self.load_friends()
        if friend_id not in friends:
            raise KeyError(f"Friend {friend_id} not found.")
        friends[friend_id]["mode"] = mode
        self.save_friends(friends)

    def mark_friend_deregistered(self, friend_id: str) -> None:
        """Mark a friend as deregistered after receiving a goodbye message.

        Sets the friend's status to 'deregistered' so the agent knows
        this friend is no longer available on the network.
        """
        friends = self.load_friends()
        if friend_id in friends:
            friends[friend_id]["status"] = "deregistered"
            friends[friend_id]["deregistered_at"] = datetime.now(timezone.utc).isoformat()
            self.save_friends(friends)

    def set_friend_token_limit(self, friend_id: str, daily_limit: int) -> None:
        friends = self.load_friends()
        if friend_id not in friends:
            raise KeyError(f"Friend {friend_id} not found.")
        friends[friend_id]["token_daily_limit"] = daily_limit
        self.save_friends(friends)

    # ── chat history ───────────────────────────────────────────

    def save_message(
        self,
        friend_id: str,
        direction: str,
        content: str,
        *,
        encrypted: bool = False,
    ) -> None:
        """Append a message to the friend's chat history.

        Args:
            friend_id: The friend's claw ID.
            direction: "sent" or "received".
            content: Plain text message content.
            encrypted: Whether the message was end-to-end encrypted.
        """
        self.ensure_dirs()
        path = self.history_dir / f"{friend_id}.jsonl"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "content": content,
            "encrypted": encrypted,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_history(
        self, friend_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return the last *limit* messages with a friend."""
        path = self.history_dir / f"{friend_id}.jsonl"
        if not path.exists():
            return []
        lines = path.read_text().strip().splitlines()
        entries = [json.loads(line) for line in lines]
        return entries[-limit:]

    # ── social rules ───────────────────────────────────────────

    def load_social_rules(self) -> str:
        if not self.rules_path.exists():
            return ""
        return self.rules_path.read_text()

    def save_social_rules(self, content: str) -> None:
        self.ensure_dirs()
        self.rules_path.write_text(content)

    # ── token budget ───────────────────────────────────────────

    def _load_token_data(self) -> dict[str, Any]:
        if not self.token_path.exists():
            return {"limits": {}, "usage": {}}
        return yaml.safe_load(self.token_path.read_text()) or {"limits": {}, "usage": {}}

    def _save_token_data(self, data: dict[str, Any]) -> None:
        self.ensure_dirs()
        self.token_path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))

    def get_token_usage(self) -> dict[str, Any]:
        """Return current token limits and usage summary."""
        data = self._load_token_data()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        usage = data.get("usage", {})
        return {
            "limits": data.get("limits", {}),
            "today": usage.get(today, {}).get("total", 0),
            "month": sum(
                day_data.get("total", 0)
                for key, day_data in usage.items()
                if key.startswith(month)
            ),
        }

    def record_token_usage(self, friend_id: str, tokens: int) -> None:
        """Record token consumption for today."""
        data = self._load_token_data()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        usage = data.setdefault("usage", {})
        day = usage.setdefault(today, {"total": 0, "by_friend": {}})
        day["total"] = day.get("total", 0) + tokens
        day["by_friend"][friend_id] = day["by_friend"].get(friend_id, 0) + tokens
        self._save_token_data(data)

    def check_token_budget(self, friend_id: str | None = None) -> dict[str, Any]:
        """Check whether token budget allows more messages.

        Returns:
            {
                "allowed": bool,
                "reason": str | None,
                "daily_remaining": int,
                "monthly_remaining": int,
            }
        """
        data = self._load_token_data()
        limits = data.get("limits", {})
        usage_info = self.get_token_usage()

        daily_limit = limits.get("daily", 100000)
        monthly_limit = limits.get("monthly", 2000000)
        daily_remaining = max(0, daily_limit - usage_info["today"])
        monthly_remaining = max(0, monthly_limit - usage_info["month"])

        if daily_remaining == 0:
            return {"allowed": False, "reason": "Daily token limit reached.", "daily_remaining": 0, "monthly_remaining": monthly_remaining}
        if monthly_remaining == 0:
            return {"allowed": False, "reason": "Monthly token limit reached.", "daily_remaining": daily_remaining, "monthly_remaining": 0}

        # per-friend check
        if friend_id:
            friends = self.load_friends()
            friend = friends.get(friend_id, {})
            per_friend_limit = friend.get("token_daily_limit") or limits.get("per_friend_daily", 20000)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            usage = data.get("usage", {})
            day = usage.get(today, {})
            friend_today = day.get("by_friend", {}).get(friend_id, 0)
            friend_remaining = max(0, per_friend_limit - friend_today)
            if friend_remaining == 0:
                return {"allowed": False, "reason": f"Daily token limit for friend {friend_id} reached.", "daily_remaining": daily_remaining, "monthly_remaining": monthly_remaining}

        return {"allowed": True, "reason": None, "daily_remaining": daily_remaining, "monthly_remaining": monthly_remaining}

    def set_token_budget(
        self,
        daily_limit: int | None = None,
        monthly_limit: int | None = None,
        per_friend_daily: int | None = None,
    ) -> dict[str, Any]:
        """Update global token limits. Returns the updated limits."""
        data = self._load_token_data()
        limits = data.setdefault("limits", {})
        if daily_limit is not None:
            limits["daily"] = daily_limit
        if monthly_limit is not None:
            limits["monthly"] = monthly_limit
        if per_friend_daily is not None:
            limits["per_friend_daily"] = per_friend_daily
        self._save_token_data(data)
        return limits
