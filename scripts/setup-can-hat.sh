#!/bin/bash
# setup-can-hat.sh - Configure CAN HAT for Raspberry Pi 5
#
# This script sets up the MCP2515 CAN controller (common on Pi CAN HATs)
# for use with SocketCAN. Run this once after first boot, then reboot.
#
# Usage: sudo ./scripts/setup-can-hat.sh
#
# Supported HATs:
#   - Waveshare RS485 CAN HAT
#   - PiCAN2 / PiCAN3
#   - Seeed Studio CAN-BUS
#   - Most MCP2515-based CAN HATs

set -e

echo "╔══════════════════════════════════════════════════╗"
echo "║   Knight Rider - CAN HAT Setup for Pi 5         ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. Detect Pi model ──────────────────────────────────────────────────────

if [ ! -f /proc/device-tree/model ]; then
    echo "⚠  Cannot detect board model. Are you running this on a Raspberry Pi?"
    echo "   Continuing anyway..."
else
    MODEL=$(tr -d '\0' < /proc/device-tree/model)
    echo "✓ Board: $MODEL"
fi

# ── 2. Install required packages ────────────────────────────────────────────

echo ""
echo "Installing can-utils..."
apt-get update -qq
apt-get install -y -qq can-utils

# ── 3. Configure /boot/firmware/config.txt (Pi 5 uses firmware path) ────────

CONFIG_FILE=""
if [ -f /boot/firmware/config.txt ]; then
    CONFIG_FILE="/boot/firmware/config.txt"
elif [ -f /boot/config.txt ]; then
    CONFIG_FILE="/boot/config.txt"
else
    echo "✗ Cannot find config.txt. Please configure the CAN HAT overlay manually."
    exit 1
fi

echo ""
echo "Configuring $CONFIG_FILE..."

# Check which oscillator frequency to use
# Most CAN HATs use either 8MHz or 16MHz crystal
echo ""
echo "Which oscillator does your CAN HAT use?"
echo "  1) 16 MHz (Waveshare, most common)"
echo "  2) 8 MHz  (some PiCAN boards)"
echo ""
read -p "Select [1/2, default=1]: " OSC_CHOICE

case "$OSC_CHOICE" in
    2)
        OSC_FREQ="8000000"
        echo "Using 8 MHz oscillator"
        ;;
    *)
        OSC_FREQ="16000000"
        echo "Using 16 MHz oscillator"
        ;;
esac

# Check which interrupt pin the HAT uses
echo ""
echo "Which GPIO interrupt pin does your CAN HAT use?"
echo "  1) GPIO 25 (most common, Waveshare)"
echo "  2) GPIO 22 (some PiCAN boards)"
echo "  3) GPIO 12"
echo ""
read -p "Select [1/2/3, default=1]: " INT_CHOICE

case "$INT_CHOICE" in
    2) INT_PIN="22" ;;
    3) INT_PIN="12" ;;
    *) INT_PIN="25" ;;
esac

echo "Using GPIO $INT_PIN for interrupt"

# Add the CAN overlay if not already present
OVERLAY_LINE="dtoverlay=mcp2515-can0,oscillator=${OSC_FREQ},interrupt=${INT_PIN}"

if grep -q "mcp2515" "$CONFIG_FILE"; then
    echo ""
    echo "⚠  Existing MCP2515 config found in $CONFIG_FILE:"
    grep "mcp2515" "$CONFIG_FILE"
    echo ""
    read -p "Replace with new config? [y/N]: " REPLACE
    if [ "$REPLACE" = "y" ] || [ "$REPLACE" = "Y" ]; then
        sed -i '/mcp2515/d' "$CONFIG_FILE"
        echo "$OVERLAY_LINE" >> "$CONFIG_FILE"
        echo "✓ Updated CAN HAT overlay"
    else
        echo "  Keeping existing config"
    fi
else
    echo "" >> "$CONFIG_FILE"
    echo "# Knight Rider CAN HAT" >> "$CONFIG_FILE"
    echo "$OVERLAY_LINE" >> "$CONFIG_FILE"
    echo "✓ Added CAN HAT overlay to $CONFIG_FILE"
fi

# Ensure SPI is enabled (required for MCP2515)
if grep -q "^dtparam=spi=on" "$CONFIG_FILE"; then
    echo "✓ SPI already enabled"
else
    if grep -q "^#dtparam=spi=on" "$CONFIG_FILE"; then
        sed -i 's/^#dtparam=spi=on/dtparam=spi=on/' "$CONFIG_FILE"
    else
        echo "dtparam=spi=on" >> "$CONFIG_FILE"
    fi
    echo "✓ Enabled SPI"
fi

# ── 4. Create systemd network config for auto-bringing up can0 ──────────────

echo ""
echo "Setting up CAN interface auto-configuration..."

# Create a script that brings up can0 at boot with OBD-II standard bitrate
cat > /etc/systemd/network/80-can.network << 'NETEOF'
[Match]
Name=can0

[CAN]
BitRate=500000
RestartSec=100ms
NETEOF

# Also create a traditional init script as fallback
cat > /usr/local/bin/can-up.sh << CANEOF
#!/bin/bash
# Bring up CAN0 interface at 500kbps (OBD-II standard)
ip link set can0 type can bitrate 500000 restart-ms 100
ip link set can0 up
CANEOF
chmod +x /usr/local/bin/can-up.sh

# Create systemd service for can-up
cat > /etc/systemd/system/can-interface.service << 'SVCEOF'
[Unit]
Description=Bring up CAN0 interface
After=network.target
Wants=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/can-up.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable can-interface.service

echo "✓ CAN interface service created and enabled"

# ── 5. Create the OBD logger systemd service ────────────────────────────────

echo ""
echo "Creating OBD logger systemd service..."

cat > /etc/systemd/system/obd-logger.service << 'LOGEOF'
[Unit]
Description=Knight Rider OBD-II Data Logger
After=can-interface.service
Requires=can-interface.service

[Service]
Type=simple
ExecStart=/usr/local/bin/obd-logger --interface can0 --log-dir /var/log/knight-rider
Restart=on-failure
RestartSec=5s
User=root

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=obd-logger

# Resource limits
MemoryMax=128M
CPUQuota=50%

[Install]
WantedBy=multi-user.target
LOGEOF

systemctl daemon-reload
echo "✓ OBD logger service created (not enabled - run 'sudo systemctl start obd-logger' manually)"

# ── 6. Summary ──────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║                  Setup Complete                  ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║                                                  ║"
echo "║  Config:  $CONFIG_FILE"
echo "║  Overlay: $OVERLAY_LINE"
echo "║                                                  ║"
echo "║  NEXT STEPS:                                     ║"
echo "║  1. Reboot:  sudo reboot                        ║"
echo "║  2. Verify:  ip link show can0                   ║"
echo "║  3. Test:    candump can0                        ║"
echo "║  4. Run:     sudo obd-logger                     ║"
echo "║                                                  ║"
echo "║  After reboot, CAN0 will auto-start at 500kbps  ║"
echo "║                                                  ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo "⚠  You MUST reboot for the CAN HAT overlay to take effect!"
echo ""
