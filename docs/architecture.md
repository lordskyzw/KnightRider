# Knight Rider Architecture

## System Overview

Knight Rider is a single-binary diagnostic tool that runs on Raspberry Pi, communicating with vehicle ECUs via CAN bus.

```
┌─────────────────────────────────────────────────────────────────┐
│                        Knight Rider                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐│
│  │   CAN    │  │   Core   │  │ Logging  │  │       UI         ││
│  │ Interface│◄─┤  Logic   ├──┤  (async) │  │ (future - HDMI)  ││
│  └────┬─────┘  └──────────┘  └──────────┘  └──────────────────┘│
└───────┼─────────────────────────────────────────────────────────┘
        │ SocketCAN
        ▼
┌───────────────┐
│  Linux Kernel │
│   CAN Driver  │
└───────┬───────┘
        │ SPI / USB
        ▼
┌───────────────┐
│  CAN Hardware │
│ (MCP2515/USB) │
└───────┬───────┘
        │ CAN Bus
        ▼
┌───────────────┐
│  Vehicle ECU  │
└───────────────┘
```

## Module Responsibilities

### `can/` — CAN Bus Interface Layer

| Module | Responsibility |
|--------|----------------|
| `interface.rs` | Raw SocketCAN socket operations (open, send, recv) |
| `isotp.rs` | ISO 15765-2 multi-frame message handling |
| `obd.rs` | OBD-II protocol: request building, response parsing |
| `scheduler.rs` | Request timing, rate limiting, timeout tracking |

### `core/` — Application Logic

| Module | Responsibility |
|--------|----------------|
| `state_machine.rs` | System states: IDLE → INIT → CONNECTED → RUNNING → ERROR |
| `signals.rs` | Decoded sensor values with timestamps |
| `datastore.rs` | In-memory storage for latest values |

### `logging/` — Data Logging

| Module | Responsibility |
|--------|----------------|
| `ringbuffer.rs` | 1MB in-memory ring buffer, non-blocking append |
| `timeseries.rs` | CSV writer with ISO 8601 timestamps, file rotation |

### `ui/` — User Interface (Future)

Reserved for HDMI-based UI. Out of scope for v1.

## Threading Model (v1)

**Single-threaded** — all operations run in a single main loop.

```
┌─────────────────────────────────────────────────────────────┐
│                        Main Loop                             │
│                                                              │
│  ┌─────────┐   ┌──────────┐   ┌─────────┐   ┌────────────┐ │
│  │ Send    │──▶│ Wait for │──▶│ Decode  │──▶│ Log/Print  │ │
│  │ Request │   │ Response │   │ Response│   │ Result     │ │
│  └─────────┘   └──────────┘   └─────────┘   └────────────┘ │
│       ▲                                            │        │
│       └────────────────────────────────────────────┘        │
│                     (100ms interval)                        │
└─────────────────────────────────────────────────────────────┘
```

**Design decisions:**
- No async runtime (tokio/async-std) — adds unnecessary complexity
- No threading primitives — avoid synchronization overhead
- Blocking I/O with timeouts — sufficient for 100ms polling rate

**Future considerations:**
- UI will require separate thread or async for rendering
- Logging flush may need async to avoid blocking CAN I/O

## Data Flow

### OBD-II Request/Response

```
1. Build OBD-II request (Mode 01, PID 0C)
   └─▶ 02 01 0C 00 00 00 00 00

2. Wrap in CAN frame (ID 0x7DF)
   └─▶ CAN Frame { id: 0x7DF, dlc: 8, data: [02, 01, 0C, ...] }

3. Send via SocketCAN
   └─▶ write() to CAN socket

4. Wait for response (timeout: 200ms)
   └─▶ read() from CAN socket

5. Receive CAN frame (ID 0x7E8)
   └─▶ CAN Frame { id: 0x7E8, dlc: 8, data: [04, 41, 0C, A, B, ...] }

6. Parse OBD-II response
   └─▶ Mode: 0x41 (response to 0x01), PID: 0x0C, Data: [A, B]

7. Decode RPM using formula
   └─▶ RPM = ((A * 256) + B) / 4

8. Store + Print + Log
```

### Error Recovery

```
┌─────────────┐
│    IDLE     │
└──────┬──────┘
       │ initialize()
       ▼
┌─────────────┐     CAN init failed
│ INITIALIZING│─────────────────────────┐
└──────┬──────┘                         │
       │ CAN ready                      │
       ▼                                │
┌─────────────┐     ECU timeout         │
│  CONNECTED  │──────────────────┐      │
└──────┬──────┘                  │      │
       │ PID 00 response         │      │
       ▼                         │      │
┌─────────────┐                  │      │
│   RUNNING   │◀─────────────────┼──────┤
└──────┬──────┘  retry after 5s  │      │
       │                         │      │
       │ CAN error / timeout     │      │
       ▼                         ▼      ▼
┌─────────────┐            ┌───────────────┐
│    ERROR    │◀───────────│ Log + Retry   │
└─────────────┘            └───────────────┘
```

## Timing Constraints

| Parameter | Value | Description |
|-----------|-------|-------------|
| Polling interval | 100ms | Time between RPM requests |
| Response timeout | 200ms | Max wait for ECU response |
| Watchdog threshold | 5s | Log WARN if no valid frames |
| Retry interval | 5s | Wait before reinitializing CAN |
| Log flush interval | 5s | Write ring buffer to disk |

## Memory Layout

```
┌────────────────────────────────────────┐
│              Stack                      │
│  - CAN frame buffers (small)           │
│  - Local variables                      │
├────────────────────────────────────────┤
│              Heap                       │
│  - Ring buffer: 1MB                    │
│  - ISO-TP reassembly: 4KB per session  │
│  - Datastore: ~1KB (latest values)     │
├────────────────────────────────────────┤
│           Static / BSS                  │
│  - Logger configuration                 │
│  - PID definition tables               │
└────────────────────────────────────────┘

Total runtime memory: < 2MB
```

## Error Handling Strategy

1. **No panics in production** — all functions return `Result<T, E>`
2. **Log everything** — with ISO 8601 timestamps
3. **Degrade gracefully** — continue if some PIDs unavailable
4. **Auto-recover** — retry CAN initialization on failure
5. **Isolate failures** — one ECU timeout doesn't affect others
