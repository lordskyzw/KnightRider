#!/bin/bash
# build-on-pi.sh - Build obd-logger directly on the Raspberry Pi
#
# This is the simplest deployment method: clone the repo on the Pi and
# build natively. No cross-compilation toolchain needed.
#
# Usage:
#   1. SSH into your Pi
#   2. Clone the repo (or copy it over)
#   3. Run: ./scripts/build-on-pi.sh
#
# This script handles installing Rust and all dependencies.

set -e

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   Knight Rider - Build on Raspberry Pi           ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. Check we're on a Pi (ARM64) ──────────────────────────────────────────

ARCH=$(uname -m)
if [ "$ARCH" != "aarch64" ]; then
    echo "⚠  Architecture is $ARCH, expected aarch64."
    echo "   This script is meant to run on the Raspberry Pi."
    read -p "Continue anyway? [y/N]: " CONT
    [ "$CONT" = "y" ] || exit 1
fi

# ── 2. Install system dependencies ──────────────────────────────────────────

echo "Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    build-essential \
    can-utils \
    pkg-config \
    curl

echo "✓ System dependencies installed"

# ── 3. Install Rust (if not already installed) ──────────────────────────────

if command -v rustc &> /dev/null; then
    RUST_VER=$(rustc --version)
    echo "✓ Rust already installed: $RUST_VER"
else
    echo "Installing Rust..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
    echo "✓ Rust installed: $(rustc --version)"
fi

# Make sure cargo is in PATH
export PATH="$HOME/.cargo/bin:$PATH"

# ── 4. Build ────────────────────────────────────────────────────────────────

echo ""
echo "Building obd-logger (release mode)..."
echo "  This may take a few minutes on the Pi..."
echo ""

# Navigate to project root (script is in scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

cargo build --release --bin obd-logger 2>&1 | tail -5

BINARY="target/release/obd-logger"
if [ ! -f "$BINARY" ]; then
    echo "✗ Build failed!"
    exit 1
fi

BINARY_SIZE=$(du -h "$BINARY" | cut -f1)
echo ""
echo "✓ Build successful: $BINARY ($BINARY_SIZE)"

# ── 5. Install ──────────────────────────────────────────────────────────────

echo ""
echo "Installing..."

sudo cp "$BINARY" /usr/local/bin/obd-logger
sudo chmod +x /usr/local/bin/obd-logger
sudo mkdir -p /var/log/knight-rider

echo "✓ Installed to /usr/local/bin/obd-logger"
echo "✓ Log directory: /var/log/knight-rider"

# ── 6. Quick test ───────────────────────────────────────────────────────────

echo ""
echo "Verifying installation..."
obd-logger --help 2>/dev/null | head -5 || echo "(help output not available on stub)"

# ── 7. Summary ──────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║              Build Complete ✓                    ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║                                                  ║"
echo "║  Binary: /usr/local/bin/obd-logger               ║"
echo "║  Logs:   /var/log/knight-rider/                  ║"
echo "║                                                  ║"
echo "║  Quick start:                                    ║"
echo "║    sudo obd-logger --help                        ║"
echo "║    sudo obd-logger -d 60                         ║"
echo "║    sudo obd-logger --passive                     ║"
echo "║                                                  ║"
echo "║  If CAN HAT is not set up yet:                   ║"
echo "║    sudo ./scripts/setup-can-hat.sh               ║"
echo "║    sudo reboot                                   ║"
echo "║                                                  ║"
echo "╚══════════════════════════════════════════════════╝"
