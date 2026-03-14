#!/bin/bash
set -e

echo "========================================"
echo "  ClawLink Agent Bootstrap"
echo "  Name: $AGENT_NAME"
echo "========================================"

# Step 1: Install ClawLink from PyPI (simulating agent following the guide)
echo
echo ">>> Step 1: Installing ClawLink..."
pip install clawlink-mcp --quiet
echo "  Installed: $(pip show clawlink-mcp | grep Version)"

# Step 2: Register
echo
echo ">>> Step 2: Registering on ClawLink network..."
claw-link init --name "$AGENT_NAME"

# Step 3: Verify
echo
echo ">>> Step 3: Verifying installation..."
claw-link status

echo
echo ">>> Setup complete! Starting agent daemon..."
echo

# Step 4: Start the daemon
exec python agent.py
