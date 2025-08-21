# Pump.fun Trading Bot - Windows Quickstart Guide

This guide covers setting up and running the pump.fun trading bot on Windows using Git Bash.

## Prerequisites

- **Python 3.11+** - Check with `python --version`
- **Git Bash** - For running commands 
- **uv** - Package manager (should be installed)

## 1. Environment Setup

### Create .env file

Create a `.env` file in the project root with the following variables:

```bash
# Required: Solana RPC endpoints (quoted URLs)
SOLANA_NODE_RPC_ENDPOINT="https://your-helius-rpc-url?api-key=YOUR_KEY"
SOLANA_NODE_WSS_ENDPOINT="wss://your-helius-wss-url?api-key=YOUR_KEY"

# Optional: Geyser for faster data streams
GEYSER_ENDPOINT=""
GEYSER_API_TOKEN=""
GEYSER_AUTH_TYPE="x-token"

# Trading: Only needed for live trading (leave empty for dry-run)
SOLANA_PRIVATE_KEY=""

# Safety and Simulation
DRY_RUN=true
DRY_RUN_STARTING_SOL=3.0
DRY_RUN_VALUE_MODE="entry"
DRY_RUN_DURATION_SECONDS=300
HALT=0
```

### Export environment variables (Git Bash)

```bash
# Export all env vars for this session
export SOLANA_NODE_RPC_ENDPOINT="https://your-helius-rpc-url?api-key=YOUR_KEY"
export SOLANA_NODE_WSS_ENDPOINT="wss://your-helius-wss-url?api-key=YOUR_KEY"
export GEYSER_ENDPOINT=""
export GEYSER_API_TOKEN=""
export SOLANA_PRIVATE_KEY=""
export DRY_RUN=true
export DRY_RUN_STARTING_SOL=3.0
export DRY_RUN_VALUE_MODE="entry"
export DRY_RUN_DURATION_SECONDS=300
export HALT=0
```

## 2. Health Checks

### A) Check dependencies
```bash
uv run python -c "import sys; print(f'Python: {sys.version}'); import uvloop" 2>/dev/null || echo "uvloop correctly disabled on Windows"
```

### B) Check environment loading
```bash
uv run python check_env.py
```
Expected output:
```
=== ENVIRONMENT VARIABLE CHECK ===
RPC: ✓
WS: ✓
GEYSER: EMPTY (optional)
HAS_PRIVATE_KEY: false
```

### C) Test listener (should detect pump.fun tokens within 2-5 minutes)
```bash
timeout 30 uv run learning-examples/listen-new-tokens/listen_logsubscribe_abc.py
```

### D) Test bot runner (should fail on empty private key)
```bash
timeout 15 uv run pump_bot
```

## 3. Dry-Run Trading

### Run with simulation (no real transactions)
```bash
# Ensure DRY_RUN=true in .env
uv run pump_bot
```

### Expected output patterns:
```
[DRY] BUY TOKEN qty=1000.0000 @ 0.000100 | value: 3.0000 → 2.9000 (Δ -0.1000) | realized_pnl_cum=0.0000 | mode=entry
[DRY] SELL TOKEN qty=1000.0000 @ 0.000100 | value: 2.9000 → 3.0000 (Δ +0.1000) | realized_pnl_cum=0.0000 | mode=entry
```

### Check simulation files:
```bash
ls -la sim_data/
# Should contain:
# - trades.ndjson (trade events)
# - portfolio_snapshots.csv (snapshots)
# - portfolio_state.json (current state)
```

## 4. Live Trading (DANGER!)

⚠️ **WARNING: Only proceed if you understand the risks!**

### Setup for live trading:
1. Add your private key to `.env`:
```bash
SOLANA_PRIVATE_KEY="your_base58_private_key_here"
DRY_RUN=false
```

2. Test with small amounts first
3. Monitor logs carefully

### Run live:
```bash
uv run pump_bot
```

## 5. Safety Features

### Emergency halt:
```bash
# Set HALT=1 to stop all trading immediately
export HALT=1
# or edit .env file
```

### Value modes:
- `entry` - Use entry price (fastest, least accurate)
- `quote` - Use curve/pool quotes (balanced)
- `rpc` - Use RPC lookups (slowest, most accurate)

## 6. Troubleshooting

### Bot won't start:
```bash
# Check environment
uv run python check_env.py

# Check bot config
uv run python src/config_loader.py bots/bot-sniper-2-logs.yaml
```

### No tokens detected:
```bash
# Test listener manually
uv run learning-examples/listen-new-tokens/listen_logsubscribe_abc.py
```

### uvloop errors on Windows:
- This is normal and handled automatically
- The bot uses a Windows-compatible event loop

## 7. Configuration Files

Bot configurations are in `bots/` directory:
- `bot-sniper-1-geyser.yaml` - Geyser listener (fastest)
- `bot-sniper-2-logs.yaml` - Log listener (standard)
- `bot-sniper-3-blocks.yaml` - Block listener
- `bot-sniper-4-pp.yaml` - PumpPortal listener

Edit these files to change:
- Trading amounts
- Slippage tolerance
- Exit strategies
- Token filters

## 8. Rollback Steps

If something goes wrong:

1. **Emergency stop:**
```bash
export HALT=1
```

2. **Return to dry-run:**
```bash
export DRY_RUN=true
export SOLANA_PRIVATE_KEY=""
```

3. **Reset simulation:**
```bash
rm -rf sim_data/
```

4. **Check wallet balance:**
```bash
# Use your preferred Solana wallet checker
```

## Support

- Check `logs/` directory for detailed bot logs
- Review `sim_data/` for simulation results
- Consult `trades/trades.log` for trade history
