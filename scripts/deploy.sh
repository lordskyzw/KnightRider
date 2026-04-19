#!/bin/bash
# deploy.sh - Cross-compile and deploy obd-logger to Raspberry Pi 5
#
# Usage:
#   ./scripts/deploy.sh <pi-ip-or-hostname>
#
# Examples:
#   ./scripts/deploy.sh 192.168.1.50
#   ./scripts/deploy.sh knightrider.local
#   PI_USER=admin ./scripts/deploy.sh 10.0.0.5
#
# Prerequisites (on your dev machine):
#   1. Rust cross-compilation target:
#      rustup target add aarch64-unknown-linux-gnu
#
#   2. Cross-linker (one of these):
#      # Ubuntu/Debian:
#      sudo apt install gcc-aarch64-linux-gnu
#      # Or use 'cross' tool:
#      cargo install cross
#
#   3. SSH key auth to the Pi (recommended):
#      ssh-copy-id pi@<pi-ip>

set -e

# ── Configuration ────────────────────────────────────────────────────────────

PI_HOST="${1:?Usage: ./scripts/deploy.sh <pi-ip-or-hostname>}"
PI_USER="${PI_USER:-pi}"
TARGET="aarch64-unknown-linux-gnu"
BINARY="obd-logger"
REMOTE_BIN="/usr/local/bin/${BINARY}"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║     Knight Rider - Deploy to Raspberry Pi        ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║  Target:  ${PI_USER}@${PI_HOST}"
echo "║  Binary:  ${BINARY}"
echo "║  Arch:    ${TARGET}"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Cross-compile ───────────────────────────────────────────────────

echo "Step 1: Cross-compiling for ${TARGET}..."

# Check if we have the cross-compilation toolchain
if command -v cross &> /dev/null; then
    echo "  Using 'cross' for compilation"
    cross build --release --target "${TARGET}" --bin "${BINARY}"
elif command -v aarch64-linux-gnu-gcc &> /dev/null; then
    echo "  Using native cross-compiler"
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
    cargo build --release --target "${TARGET}" --bin "${BINARY}"
else
    echo "✗ No cross-compiler found!"
    echo ""
    echo "  Install one of these:"
    echo "    Option A: cargo install cross"
    echo "    Option B: sudo apt install gcc-aarch64-linux-gnu"
    echo ""
    echo "  Or build directly on the Pi (see below)."
    exit 1
fi

BINARY_PATH="target/${TARGET}/release/${BINARY}"

if [ ! -f "${BINARY_PATH}" ]; then
    echo "✗ Binary not found at ${BINARY_PATH}"
    exit 1
fi

BINARY_SIZE=$(du -h "${BINARY_PATH}" | cut -f1)
echo "✓ Compiled successfully (${BINARY_SIZE})"

# ── Step 2: Deploy to Pi ────────────────────────────────────────────────────

echo ""
echo "Step 2: Deploying to ${PI_USER}@${PI_HOST}..."

# Copy binary
scp "${BINARY_PATH}" "${PI_USER}@${PI_HOST}:/tmp/${BINARY}"

# Install binary and set permissions
ssh "${PI_USER}@${PI_HOST}" << REMOTE
    sudo mv /tmp/${BINARY} ${REMOTE_BIN}
    sudo chmod +x ${REMOTE_BIN}
    sudo chown root:root ${REMOTE_BIN}

    # Create log directory
    sudo mkdir -p /var/log/knight-rider
    sudo chown root:root /var/log/knight-rider

    echo "✓ Installed to ${REMOTE_BIN}"
    echo "✓ Log directory: /var/log/knight-rider"
REMOTE

echo "✓ Deployment complete"

# ── Step 3: Verify ──────────────────────────────────────────────────────────

echo ""
echo "Step 3: Verifying installation..."

ssh "${PI_USER}@${PI_HOST}" << 'VERIFY'
    echo ""
    echo "Binary:"
    ls -la /usr/local/bin/obd-logger
    file /usr/local/bin/obd-logger
    echo ""

    echo "CAN interface status:"
    if ip link show can0 2>/dev/null; then
        echo "✓ can0 is available"
    else
        echo "⚠ can0 not found (run setup-can-hat.sh first, then reboot)"
    fi

    echo ""
    echo "Ready to log! Run:"
    echo "  sudo obd-logger --help"
    echo "  sudo obd-logger -d 60          # Log for 60 seconds"
    echo "  sudo obd-logger                 # Log until Ctrl+C"
VERIFY

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║              Deployment Complete ✓               ║"
echo "║                                                  ║"
echo "║  SSH in and run:                                 ║"
echo "║    ssh ${PI_USER}@${PI_HOST}"
echo "║    sudo obd-logger                               ║"
echo "╚══════════════════════════════════════════════════╝"
