# 🚘 The Life of a Diagnostic Session

This document narrates the journey of a Knight Rider session, from physical connection to data visualization, illustrating the system's internal flow.

## Phase 1: The Awakening (Initialization)
**Function:** `main()` in `src/main.rs`
**Background Activity:** Linux Kernel CAN Driver (`mcp251x` or `vcan`)

The software wakes up. It doesn't know what car it's in. It initializes its nervous system:
1.  **Logging**: It opens `/tmp/knight-rider-raw.log` via `TimeseriesLogger::new()`. It’s ready to record every whisper on the wire.
2.  **Interface**: It calls `CanInterface::open("can0")`. This asks the Linux kernel for a handle to the CAN controller.
3.  **State Machine**: The `StateMachine` transitions from `IDLE` to `INITIALIZING`.

> **Visual:** Like a submarine opening its sonar. The system is listening, but the ocean (the CAN bus) is silent to us until we speak.

---

## Phase 2: The Handshake (Discovery)
**Function:** `query_supported_pids()`
**Protocol:** OBD-II Mode 01 PID 00

Knight Rider needs to know who is out there. It constructs a **Functional Broadcast** message.
*   **The Request**: "Is anyone listening? What PIDs do you support?"
*   **The Code**:
    ```rust
    let request = ObdRequest::current_data(ObdPid::SupportedPids01To20);
    // Address 0x7DF (Everyone)
    can.send(&frame);
    ```

**On the Wire:**
A packet flies out with ID `0x7DF`.
The Engine Control Unit (ECU) hears it. It responds with ID `0x7E8`.

**The Software Reaction:**
1.  `can.recv()` wakes up with new data.
2.  `isotp.receive()` looks at the packet. It sees it's a **Single Frame**. No reassembly needed.
3.  `ObdResponse::parse()` reads the bitmask. "Aha! You support RPM, Speed, and Temp."
4.  The `StateMachine` shifts to `CONNECTED`.

---

## Phase 3: The Heartbeat (The Main Loop)
**Function:** `run()` loop
**Time:** Every 100 milliseconds

Now the system enters its rhythmic trance. It must query the engine 10 times a second without faltering.

### 1. The Scheduler Checks the Clock
*   `scheduler.wait_for_next()`: The code pauses. Is it time? 98ms... 99ms... **100ms**. Go.

### 2. Formatting the Question
*   We want RPM. That's Mode `01`, PID `0C`.
*   `ObdRequest::to_can_data()` formats the bytes: `[02, 01, 0C, 00, 00...]`.
    *   `02`: Length (2 bytes follow).
    *   `01`: Service (Show Current Data).
    *   `0C`: PID (Engine RPM).

### 3. Transmission
*   `can.send()`: The packet is handed to the kernel. The CAN controller asserts electrical pulses on the CAN-H and CAN-L wires. The message leaves the box.

---

## Phase 4: The Wait & The Reassembly
**Function:** `wait_for_response()`
**Background:** `pthreads` (sleeping)

The software blocks on `can.recv()`. It has a 200ms timeout.
*   **Scenario A: Silence.** If the ECU is busy, the clock ticks. 199ms... 200ms. Timeout! We log it and move on.
*   **Scenario B: Success.** The ECU replies.

**The ECU Reply (`0x7E8`):**
`[04, 41, 0C, 0B, B8, 00...]`
*   `04`: Length 4.
*   `41`: "Here is Mode 1 data" (01 + 40).
*   `0C`: "This is for RPM".
*   `0B B8`: The value (3000 in hex).

**Processing:**
1.  **Raw Logging**: Before *thinking*, we *write*. The raw hex hits the `TimeseriesLogger` buffer immediately. We never lose evidence.
2.  **ISO-TP Layer**: `IsoTpSession` checks bits. "Is this part of a larger message?" No, it fits in one frame. It hands up the clean payload.

---

## Phase 5: Decoding the Secret
**Function:** `DecodedValue::decode()`
**Logic:** `src/can/obd.rs`

The software holds the raw bytes `0B` and `B8`. It looks up the formula for RPM:
`((A * 256) + B) / 4`

1.  A = `0x0B` (11)
2.  B = `0xB8` (184)
3.  Calculation: `((11 * 256) + 184) / 4`
4.  Result: `750 RPM` (Idle speed).

The `Signal` is created: `Signal { value: 750.0, unit: "rpm", ... }`.

---

## Phase 6: The Scribe (Background Activities)
**Function:** `TimeseriesLogger::log_frame()`
**Background:** Disk I/O

While the math was happening, the logger was busy.
*   **RAM Buffer**: We don't write to the SD card for every single packet (that would kill the card). We write to a RAM buffer (`BufWriter`).
*   **The Flush**: Every few seconds, or when the buffer fills, the OS flushes that buffer to the physical file `knight-rider-raw.log`.

**Result:**
The console prints: `[2025-12-31T16:20:00Z] RPM: 750 rpm`

The loop finishes. `scheduler.mark_sent()`. The system sleeps.
...and 100ms later, it does it all again.

---

## Resilience: What if it fails?

If the **Connector Jiggles Loose**:
1.  `can.send()` returns an error ("No buffer space" or "Network down").
2.  `StateMachine` records an error. `ErrorCount` goes 1... 2... 3...
3.  At 5 errors, state goes to `ERROR`.
4.  The logic pauses. It waits 1 second. It attempts to re-initialize the socket.
5.  It calls `query_supported_pids()` again.
6.  If the connection is back, it heals itself and resumes `RUNNING`.
