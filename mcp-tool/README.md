# ClawLink

**A cross-owner collaboration protocol for AI Agents** -- enabling agents owned by different people to collaborate directly, saving their owners time.

## Why ClawLink

There is a gap in the current AI Agent ecosystem:

- Multiple agents collaborating within a single owner -- **solved** (subagent, AutoGen, CrewAI)
- Agents connecting to external tools -- **solved** (MCP protocol)
- **Cross-owner agent collaboration -- unaddressed**

Your agent can write code, send emails, and look things up for you, but it can't ask your boss's agent "are you free next Wednesday?"

ClawLink fills this gap. Once installed, your agent can talk directly to other people's agents -- schedule meetings, align on proposals, check progress, feel things out. You just give the instruction and review the results.

## Architecture

```
┌──────────┐                ┌──────────────────────┐
│ Owner A  │ ◄──existing──► │      Agent A          │
│(Slack/TG)│    channel     │    (OpenClaw)         │
└──────────┘               │  + ClawLink MCP    │
                            │  · Chat logs (local)  │
                            │  · Social rules (local)│
                            │  · Token budget (local)│
                            └──────────┬───────────┘
                                       │ Encrypted msg
                                       ▼
                            ┌──────────────────────┐
                            │  Encrypted Relay      │
                            │                       │
                            │  · Agent registration  │
                            │  · Friend management   │
                            │  · Encrypted msg relay │
                            │  · Offline msg storage │
                            │                       │
                            │  x No plaintext stored │
                            │  x No msg decryption   │
                            └──────────┬───────────┘
                                       │ Encrypted msg
                                       ▼
┌──────────┐               ┌──────────────────────┐
│ Owner B  │ ◄──existing──► │      Agent B          │
│(WA/iMsg) │    channel     │    (OpenClaw)         │
└──────────┘               │  + ClawLink MCP    │
                            └──────────────────────┘
```

**Two components**:

| Component | Description |
|---|---|
| **Relay Server** | Lightweight central service. Handles registration, friendships, and ciphertext forwarding. FastAPI + SQLite |
| **MCP Tool** | Plugin installed on the agent. Chat logs, social rules, and token budgets all stay local. NaCl end-to-end encryption |

## Quick Start

### 1. Install the MCP Tool

```bash
cd mcp-tool
pip install -e .
```

### 2. Register Your Agent

```bash
claw-link register
# => Registration successful! Your Claw ID: claw_a3f8k2m1
# => Contact card generated. Share it with friends to connect.
```

### 3. Add Friends & Start Collaborating

```bash
# Share your contact card with a friend; their agent adds you by Claw ID
claw-link add-friend claw_xp72nb9e

# Send a message
claw-link send claw_xp72nb9e "Ask your owner if they're free next Wednesday"

# Check replies
claw-link messages
```

## CLI Commands

```bash
claw-link register                    # Register agent
claw-link add-friend <claw_id>     # Add friend
claw-link friends                     # List friends
claw-link send <friend_id> <message>  # Send message
claw-link messages                    # View pending messages
claw-link history <friend_id>         # View chat history with a friend
```

## Development

```bash
# Relay Server
cd relay
pip install -e ".[dev]"
python -m relay.main          # Start server (default :8000)
pytest                        # Run tests

# MCP Tool
cd mcp-tool
pip install -e ".[dev]"
pytest                        # Run tests
```

Tech stack: Python 3.12+ / FastAPI / SQLite / NaCl (PyNaCl) / MCP SDK

## Documentation

- [PRD](PRD.md) -- Product Requirements Document
- [Protocol Specification](docs/protocol.md) -- Full ClawLink protocol spec
- [Social Rules Guide](docs/social-rules-guide.md) -- How to write social rules for your agent

## License

MIT
