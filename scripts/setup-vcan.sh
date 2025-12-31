#!/bin/bash
# setup-vcan.sh - Create virtual CAN interface for testing
# Usage: sudo ./scripts/setup-vcan.sh
#
# This script creates a virtual CAN interface (vcan0) for testing
# Knight Rider without real CAN hardware.

set -e

INTERFACE="vcan0"

echo "Setting up virtual CAN interface: $INTERFACE"

# Load the vcan kernel module
if ! lsmod | grep -q "^vcan"; then
    echo "Loading vcan kernel module..."
    modprobe vcan
fi

# Check if interface already exists
if ip link show "$INTERFACE" &>/dev/null; then
    echo "Interface $INTERFACE already exists"
    
    # Bring it down first to reset state
    ip link set "$INTERFACE" down 2>/dev/null || true
fi

# Create the virtual CAN interface if it doesn't exist
if ! ip link show "$INTERFACE" &>/dev/null; then
    echo "Creating $INTERFACE..."
    ip link add dev "$INTERFACE" type vcan
fi

# Bring up the interface
echo "Bringing up $INTERFACE..."
ip link set "$INTERFACE" up

# Verify the interface is up
if ip link show "$INTERFACE" | grep -q "UP"; then
    echo "✓ $INTERFACE is up and ready"
    echo ""
    echo "To test, run in two terminals:"
    echo "  Terminal 1: candump $INTERFACE"
    echo "  Terminal 2: cansend $INTERFACE 7DF#0201200000000000"
    echo ""
    echo "To simulate RPM response (3000 RPM):"
    echo "  cansend $INTERFACE 7E8#04410C2EE0000000"
else
    echo "✗ Failed to bring up $INTERFACE"
    exit 1
fi
