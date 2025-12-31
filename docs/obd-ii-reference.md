# OBD-II Protocol Reference

This document covers the OBD-II protocol as implemented in Knight Rider.

## Standards

| Standard | Description |
|----------|-------------|
| ISO 15765-4 | CAN-based OBD-II (physical + network layer) |
| ISO 15765-2 | ISO-TP (Transport Protocol for multi-frame) |
| ISO 15031 / SAE J1979 | OBD-II diagnostic services (application layer) |

## CAN Addressing

### Request IDs

| ID | Description |
|----|-------------|
| `0x7DF` | Functional broadcast — queries all ECUs |
| `0x7E0-0x7E7` | Physical addressing — specific ECU (rarely used) |

### Response IDs

| ID | ECU |
|----|-----|
| `0x7E8` | Engine Control Module (ECM) |
| `0x7E9` | Transmission Control Module (TCM) |
| `0x7EA` | Hybrid/Emissions (varies) |
| `0x7EB-0x7EF` | Manufacturer-specific |

## OBD-II Frame Format

### Request Frame

```
Byte:   0     1     2     3     4     5     6     7
      ┌─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┐
      │ Len │Mode │ PID │ 00  │ 00  │ 00  │ 00  │ 00  │
      └─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┘

Example - Request Engine RPM (Mode 01, PID 0C):
CAN ID: 0x7DF
Data:   02 01 0C 00 00 00 00 00
        ▲  ▲  ▲
        │  │  └─ PID 0x0C (Engine RPM)
        │  └──── Mode 0x01 (Current data)
        └─────── Length: 2 bytes follow
```

### Response Frame

```
Byte:   0     1     2     3     4     5     6     7
      ┌─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┐
      │ Len │Mode │ PID │  A  │  B  │  C  │  D  │ ... │
      └─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┘

Example - Engine RPM Response (3000 RPM):
CAN ID: 0x7E8
Data:   04 41 0C 2E E0 00 00 00
        ▲  ▲  ▲  ▲──▲
        │  │  │  │  └─ B = 0xE0
        │  │  │  └──── A = 0x2E
        │  │  └─────── PID 0x0C
        │  └────────── Mode 0x41 (response to 0x01)
        └───────────── Length: 4 bytes follow

RPM = ((A * 256) + B) / 4 = ((0x2E * 256) + 0xE0) / 4 = 12000 / 4 = 3000
```

## Mode 01 PIDs — Current Data

### PID 0x00 — Supported PIDs [01-20]

Returns a 4-byte bitmask indicating which PIDs 0x01-0x20 are supported.

```
Request:  02 01 00 00 00 00 00 00
Response: 06 41 00 BE 1F B8 10 00
                 ▲──▲──▲──▲
                 Bitmask: 0xBE1FB810

Bit interpretation (MSB first):
  Bit 0 (0x80000000) = PID 0x01 supported
  Bit 1 (0x40000000) = PID 0x02 supported
  ...
  Bit 31 (0x00000001) = PID 0x20 supported
```

### PID 0x0C — Engine RPM (Required)

| Property | Value |
|----------|-------|
| Bytes | 2 (A, B) |
| Formula | `((A * 256) + B) / 4` |
| Unit | RPM |
| Range | 0 - 16,383.75 RPM |
| Resolution | 0.25 RPM |

```
Example: A=0x2E, B=0xE0
RPM = ((0x2E * 256) + 0xE0) / 4 = (11776 + 224) / 4 = 3000 RPM
```

### PID 0x0D — Vehicle Speed (Required)

| Property | Value |
|----------|-------|
| Bytes | 1 (A) |
| Formula | `A` |
| Unit | km/h |
| Range | 0 - 255 km/h |

```
Example: A=0x64
Speed = 100 km/h
```

### PID 0x05 — Engine Coolant Temperature

| Property | Value |
|----------|-------|
| Bytes | 1 (A) |
| Formula | `A - 40` |
| Unit | °C |
| Range | -40 to 215 °C |

```
Example: A=0x64
Temperature = 100 - 40 = 60°C
```

### PID 0x0F — Intake Air Temperature

| Property | Value |
|----------|-------|
| Bytes | 1 (A) |
| Formula | `A - 40` |
| Unit | °C |
| Range | -40 to 215 °C |

### PID 0x11 — Throttle Position

| Property | Value |
|----------|-------|
| Bytes | 1 (A) |
| Formula | `(A * 100) / 255` |
| Unit | % |
| Range | 0 - 100% |

```
Example: A=0x80
Throttle = (128 * 100) / 255 = 50.2%
```

### PID 0x2F — Fuel Tank Level

| Property | Value |
|----------|-------|
| Bytes | 1 (A) |
| Formula | `(A * 100) / 255` |
| Unit | % |
| Range | 0 - 100% |

## ISO-TP (ISO 15765-2) — Multi-Frame Messages

For responses longer than 7 bytes, ISO-TP is used.

### Frame Types

| Type | First Nibble | Description |
|------|--------------|-------------|
| Single Frame (SF) | `0x0` | Complete message ≤7 bytes |
| First Frame (FF) | `0x1` | First fragment of multi-frame |
| Consecutive Frame (CF) | `0x2` | Subsequent fragments |
| Flow Control (FC) | `0x3` | Receiver → Sender flow control |

### Single Frame (SF)

```
Byte:   0        1-7
      ┌────────┬────────────────────┐
      │ 0L     │     Data           │
      └────────┴────────────────────┘

L = length (0-7)

Example: 04 41 0C 2E E0 00 00 00
         ▲
         0x04 = SF, length 4
```

### First Frame (FF)

```
Byte:   0     1     2-7
      ┌─────┬─────┬─────────────────┐
      │ 1H  │  L  │     Data        │
      └─────┴─────┴─────────────────┘

H:L = total message length (12-bit)

Example: 10 14 41 00 BE 1F B8 10 ...
         ▲──▲
         0x1014 = FF, total length 20 bytes
```

### Consecutive Frame (CF)

```
Byte:   0     1-7
      ┌─────┬─────────────────────┐
      │ 2N  │       Data          │
      └─────┴─────────────────────┘

N = sequence number (0-F, wraps)

Example: 21 C0 00 00 00 00 00 00
         ▲
         0x21 = CF, sequence 1
```

### Flow Control (FC)

```
Byte:   0     1     2     3-7
      ┌─────┬─────┬─────┬─────────┐
      │ 3FS │ BS  │ ST  │ unused  │
      └─────┴─────┴─────┴─────────┘

FS = Flow Status (0=CTS, 1=Wait, 2=Overflow)
BS = Block Size (0=send all)
ST = Separation Time (0=send immediately)

Standard FC for OBD-II: 30 00 00 00 00 00 00 00
                        ▲  ▲  ▲
                        │  │  └─ ST=0 (no delay)
                        │  └──── BS=0 (send all)
                        └─────── FS=0 (clear to send)
```

## Timing Requirements

| Parameter | Value | Description |
|-----------|-------|-------------|
| P2_CAN_CLIENT | 50ms | ECU must start responding within |
| P2_CAN_SERVER | 5000ms | Max time for complete response |
| Request interval | 100ms | Minimum gap between requests |
| Response timeout | 200ms | Mark as TIMEOUT after |

## Error Responses

When an ECU cannot fulfill a request, it responds with a Negative Response:

```
Response: 03 7F 01 12 00 00 00 00
              ▲  ▲  ▲
              │  │  └─ Error code: 0x12 (subFunctionNotSupported)
              │  └──── Service ID that failed
              └─────── 0x7F = Negative Response Service ID
```

### Common Error Codes

| Code | Description |
|------|-------------|
| `0x10` | General reject |
| `0x11` | Service not supported |
| `0x12` | Sub-function not supported |
| `0x22` | Conditions not correct |
| `0x31` | Request out of range (PID not supported) |

## CAN Bus Speeds

| Speed | Usage |
|-------|-------|
| 500 kbps | Default (ISO 15765-4 standard) |
| 250 kbps | Fallback (some older/European vehicles) |

**Auto-detection strategy:**
1. Open interface at 500 kbps
2. Send PID 0x00 request
3. If no response in 1s, switch to 250 kbps
4. Retry PID 0x00 request
5. If still no response, report error
