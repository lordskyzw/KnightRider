# Knight Rider.

A field-grade ECU diagnostic computer for Raspberry Pi.

## What is Knight Rider?

Knight Rider is a self-contained automotive diagnostic tool that:

- Runs on Raspberry Pi 4/5 with Linux (Raspberry Pi OS Lite)
- Communicates directly with vehicle ECUs via CAN / OBD-II
- Operates fully offline — no phone, no cloud, no external dependencies
- Logs data locally with rotation
- Designed for reliability in hostile automotive environments

## Hardware Requirements

| Component | Specification |
|-----------|---------------|
| Computer | Raspberry Pi 4 or 5 |
| CAN Interface | MCP2515 CAN HAT (SPI) or USB-to-CAN adapter |
| OBD-II Connector | Standard 16-pin J1962 (Pins 6 & 14 = CAN-H/CAN-L) |
| Display | HDMI monitor (touch not required) |
| Power | 12V → 5V buck converter, 3A minimum |

> ⚠️ **Never power the Pi directly from the car battery.** Use a proper buck converter.

## Supported Protocols

- **ISO 15765-4** (CAN-based OBD-II) — 85%+ of vehicles since 2008
- **CAN speeds:** 500 kbps (default), 250 kbps (fallback)
- **OBD-II Mode 01** — Real-time data (RPM, speed, temperature, etc.)

Non-CAN protocols (K-Line, J1850) are explicitly out of scope for v1.

## Building

```bash
# Native build (on Raspberry Pi or Linux)
cargo build --release

# The binary will be at ./target/release/knight-rider
```

### Cross-compilation from Windows/macOS

Knight Rider uses SocketCAN which is Linux-only. For cross-compilation:

```bash
# Install ARM target
rustup target add aarch64-unknown-linux-gnu

# Cross-compile (requires linker setup)
cargo build --release --target aarch64-unknown-linux-gnu
```

## Usage

```bash
# Run with real CAN interface
./knight-rider --interface can0

# Run with virtual CAN (for testing)
./knight-rider --interface vcan0

# Enable debug logging
RUST_LOG=debug ./knight-rider --interface vcan0
```

## Testing with Virtual CAN

On Linux, you can test without hardware using virtual CAN:

```bash
# Setup virtual CAN interface
sudo ./scripts/setup-vcan.sh

# In terminal 1: Run Knight Rider
./target/release/knight-rider --interface vcan0

# In terminal 2: Simulate ECU response (3000 RPM)
# Formula: RPM = ((A * 256) + B) / 4
# 3000 * 4 = 12000 = 0x2EE0
cansend vcan0 7E8#04410C2EE0000000
```

## Output Format

Console output:
```
[2025-12-31T14:23:45.123Z] RPM: 1234
[2025-12-31T14:23:45.323Z] RPM: 1250
[2025-12-31T14:23:45.523Z] RPM: TIMEOUT
```

Raw CAN log (`/tmp/knight-rider-raw.log`):
```
timestamp,can_id,dlc,data
2025-12-31T14:23:45.123Z,0x7E8,8,41 0C 12 34 00 00 00 00
```

## Project Structure

```
knight-rider/
├── src/
│   ├── can/           # CAN bus interface layer
│   │   ├── interface.rs   # SocketCAN wrapper
│   │   ├── isotp.rs       # ISO 15765-2 multi-frame
│   │   ├── obd.rs         # OBD-II protocol
│   │   └── scheduler.rs   # Request timing
│   ├── core/          # Application logic
│   │   ├── state_machine.rs
│   │   ├── signals.rs
│   │   └── datastore.rs
│   ├── logging/       # Data logging
│   │   ├── ringbuffer.rs
│   │   └── timeseries.rs
│   └── main.rs
├── docs/              # Documentation
├── tests/             # Integration tests
└── scripts/           # Setup scripts
```

## License

MIT
