//! OBD-II Data Logger
//!
//! A focused tool for reading and logging all available OBD-II data from a
//! vehicle via the CAN HAT on a Raspberry Pi 5.
//!
//! This binary:
//!   1. Opens the CAN interface (can0 by default)
//!   2. Discovers which PIDs the vehicle ECU supports
//!   3. Polls all supported PIDs in a round-robin loop
//!   4. Logs raw CAN frames to a CSV file
//!   5. Logs decoded values to a human-readable log file
//!   6. Prints live values to the console
//!
//! # Usage
//!
//! ```bash
//! # Basic usage (defaults to can0, logs to /var/log/knight-rider/)
//! sudo ./obd-logger
//!
//! # Specify interface and duration
//! sudo ./obd-logger --interface can0 --duration 300
//!
//! # Custom log directory
//! sudo ./obd-logger --log-dir /home/pi/obd-logs
//!
//! # Passive mode: just listen, don't send requests
//! sudo ./obd-logger --passive
//!
//! # Debug logging
//! RUST_LOG=debug sudo ./obd-logger
//! ```

use std::fs::{self, OpenOptions};
use std::io::{BufWriter, Write};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use chrono::Utc;

use knight_rider::can::{CanFrame, CanInterface, ObdPid, ObdRequest, ObdResponse, IsoTpSession};
use knight_rider::can::obd::{self, addressing};
use knight_rider::logging::timeseries::{RawFrameEntry, TimeseriesLogger};

// ── All known PIDs we'll try to poll ────────────────────────────────────────

/// PIDs to query, in priority order.
const POLL_PIDS: &[(ObdPid, &str)] = &[
    (ObdPid::EngineRpm,             "Engine RPM"),
    (ObdPid::VehicleSpeed,          "Vehicle Speed"),
    (ObdPid::CoolantTemperature,    "Coolant Temperature"),
    (ObdPid::IntakeAirTemperature,  "Intake Air Temperature"),
    (ObdPid::ThrottlePosition,      "Throttle Position"),
    (ObdPid::FuelTankLevel,         "Fuel Tank Level"),
];

// ── Configuration ───────────────────────────────────────────────────────────

struct LoggerConfig {
    interface_name: String,
    run_duration: Option<Duration>,  // None = run forever
    log_dir: PathBuf,
    passive: bool,                   // If true, only listen, don't send
    poll_interval_ms: u64,
}

impl LoggerConfig {
    fn from_args() -> Self {
        let args: Vec<String> = std::env::args().collect();

        let interface_name = get_arg(&args, &["--interface", "-i"])
            .unwrap_or_else(|| "can0".to_string());

        let run_duration = get_arg(&args, &["--duration", "-d"])
            .and_then(|s| s.parse::<u64>().ok())
            .map(Duration::from_secs);

        let log_dir = get_arg(&args, &["--log-dir", "-o"])
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/var/log/knight-rider"));

        let passive = args.iter().any(|a| a == "--passive" || a == "-p");

        let poll_interval_ms = get_arg(&args, &["--interval"])
            .and_then(|s| s.parse().ok())
            .unwrap_or(150); // 150ms between requests

        Self {
            interface_name,
            run_duration,
            log_dir,
            passive,
            poll_interval_ms,
        }
    }
}

fn get_arg(args: &[String], flags: &[&str]) -> Option<String> {
    args.iter()
        .position(|a| flags.contains(&a.as_str()))
        .and_then(|i| args.get(i + 1))
        .cloned()
}

fn print_help() {
    println!(r#"
Knight Rider OBD-II Data Logger
===============================

Reads and logs all available OBD-II data from a vehicle.

USAGE:
    sudo ./obd-logger [OPTIONS]

OPTIONS:
    -i, --interface <NAME>    CAN interface name [default: can0]
    -d, --duration <SECS>     Run duration in seconds [default: forever]
    -o, --log-dir <PATH>      Log output directory [default: /var/log/knight-rider]
    -p, --passive             Passive mode: only listen, don't send requests
        --interval <MS>       Polling interval in milliseconds [default: 150]
    -c, --check               Run hardware diagnostics (check HAT, power, CAN)
    -h, --help                Show this help message

EXAMPLES:
    sudo ./obd-logger --check                  # Verify CAN HAT is working
    sudo ./obd-logger                          # Basic usage
    sudo ./obd-logger -d 60                    # Log for 60 seconds
    sudo ./obd-logger --passive                # Just sniff the bus
    sudo ./obd-logger -i vcan0 --interval 100  # Testing with virtual CAN
"#);
}

// ── Main ────────────────────────────────────────────────────────────────────

fn main() {
    // Initialize logging
    env_logger::Builder::from_env(
        env_logger::Env::default().default_filter_or("info")
    )
    .format(|buf, record| {
        writeln!(
            buf,
            "[{}] {} - {}",
            Utc::now().format("%H:%M:%S%.3f"),
            record.level(),
            record.args()
        )
    })
    .init();

    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--help" || a == "-h") {
        print_help();
        return;
    }

    if args.iter().any(|a| a == "--check" || a == "-c") {
        let interface = get_arg(&args, &["--interface", "-i"])
            .unwrap_or_else(|| "can0".to_string());
        run_diagnostics(&interface);
        return;
    }

    let config = LoggerConfig::from_args();

    println!();
    println!("╔══════════════════════════════════════════════════╗");
    println!("║     Knight Rider OBD-II Data Logger v0.1.0      ║");
    println!("╠══════════════════════════════════════════════════╣");
    println!("║  Interface:  {:<36}║", config.interface_name);
    println!("║  Log dir:    {:<36}║", config.log_dir.display());
    println!("║  Mode:       {:<36}║",
        if config.passive { "PASSIVE (listen only)" } else { "ACTIVE (poll PIDs)" });
    println!("║  Duration:   {:<36}║",
        config.run_duration
            .map(|d| format!("{} seconds", d.as_secs()))
            .unwrap_or_else(|| "unlimited (Ctrl+C to stop)".to_string()));
    println!("╚══════════════════════════════════════════════════╝");
    println!();

    // Set up Ctrl+C handler
    let running = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(true));
    let r = running.clone();
    ctrlc_setup(r);

    if let Err(e) = run_logger(config, running) {
        log::error!("Logger error: {}", e);
        std::process::exit(1);
    }
}

/// Simple Ctrl+C handler without external deps.
fn ctrlc_setup(running: std::sync::Arc<std::sync::atomic::AtomicBool>) {
    #[cfg(target_os = "linux")]
    {
        use std::sync::atomic::Ordering;
        unsafe {
            libc::signal(libc::SIGINT, signal_handler as libc::sighandler_t);
            libc::signal(libc::SIGTERM, signal_handler as libc::sighandler_t);
        }
        RUNNING_FLAG.store(running.as_ref() as *const _ as usize, Ordering::SeqCst);
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = running;
    }
}

#[cfg(target_os = "linux")]
static RUNNING_FLAG: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

#[cfg(target_os = "linux")]
extern "C" fn signal_handler(_: libc::c_int) {
    use std::sync::atomic::Ordering;
    let ptr = RUNNING_FLAG.load(Ordering::SeqCst) as *const std::sync::atomic::AtomicBool;
    if !ptr.is_null() {
        unsafe { &*ptr }.store(false, Ordering::SeqCst);
    }
    // Write directly to stderr to avoid allocator issues in signal handlers
    let _ = std::io::stderr().write_all(b"\n\n  Shutting down gracefully...\n\n");
}

// ── Logger logic ────────────────────────────────────────────────────────────

fn run_logger(
    config: LoggerConfig,
    running: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> Result<(), Box<dyn std::error::Error>> {
    // Create log directory
    fs::create_dir_all(&config.log_dir)?;

    let session_ts = Utc::now().format("%Y%m%d_%H%M%S").to_string();

    // Open log files
    let raw_csv_path = config.log_dir.join(format!("raw_frames_{}.csv", session_ts));
    let decoded_log_path = config.log_dir.join(format!("decoded_{}.log", session_ts));
    let summary_path = config.log_dir.join(format!("session_{}.txt", session_ts));

    let mut raw_logger = TimeseriesLogger::new(raw_csv_path.clone())?;
    let mut decoded_writer = BufWriter::new(
        OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&decoded_log_path)?
    );

    writeln!(decoded_writer, "# Knight Rider OBD-II Decoded Log")?;
    writeln!(decoded_writer, "# Session: {}", session_ts)?;
    writeln!(decoded_writer, "# Interface: {}", config.interface_name)?;
    writeln!(decoded_writer, "# Format: timestamp | pid_name | raw_hex | decoded_value unit")?;
    writeln!(decoded_writer, "#")?;

    log::info!("Raw frames  -> {}", raw_csv_path.display());
    log::info!("Decoded log -> {}", decoded_log_path.display());

    // Open CAN interface
    log::info!("Opening CAN interface: {}", config.interface_name);
    let mut can = CanInterface::open(&config.interface_name)?;
    can.set_read_timeout(Some(Duration::from_millis(200)))?;
    log::info!("CAN interface opened successfully");

    let mut isotp = IsoTpSession::new();

    // ── Phase 1: Discover supported PIDs ────────────────────────────────
    let supported_pids = if !config.passive {
        log::info!("Discovering supported PIDs...");
        let pids = discover_supported_pids(&can, &mut isotp, &mut raw_logger);
        if pids.is_empty() {
            log::warn!("No supported PIDs discovered. Will try all known PIDs anyway.");
            POLL_PIDS.iter().map(|(pid, _)| *pid).collect::<Vec<_>>()
        } else {
            log::info!("Discovered {} supported PIDs: {:?}", pids.len(),
                pids.iter().map(|p| p.name()).collect::<Vec<_>>());
            pids
        }
    } else {
        log::info!("Passive mode: skipping PID discovery");
        Vec::new()
    };

    // ── Phase 2: Main logging loop ──────────────────────────────────────
    let start_time = Instant::now();
    let poll_interval = Duration::from_millis(config.poll_interval_ms);
    let mut total_frames: u64 = 0;
    let mut total_decoded: u64 = 0;
    let mut total_errors: u64 = 0;
    let mut pid_index: usize = 0;

    log::info!("Starting data capture...");
    println!();
    println!("  {:<25} {:<15} {:<10} {:<20}", "TIMESTAMP", "PARAMETER", "VALUE", "RAW HEX");
    println!("  {}", "-".repeat(70));

    while running.load(std::sync::atomic::Ordering::Relaxed) {
        // Check duration limit
        if let Some(duration) = config.run_duration {
            if start_time.elapsed() >= duration {
                log::info!("Duration limit reached");
                break;
            }
        }

        if config.passive {
            // ── Passive mode: just read whatever is on the bus ───────────
            match can.recv() {
                Ok(frame) => {
                    total_frames += 1;
                    log_raw_frame(&frame, &mut raw_logger);

                    if addressing::is_obd_response(frame.id) {
                        isotp.reset();
                        if let Ok(Some(payload)) = isotp.receive(frame.data()) {
                            try_decode_and_log(
                                frame.id, &payload, &frame,
                                &mut decoded_writer, &mut total_decoded,
                            );
                        }
                    } else {
                        let ts = Utc::now().format("%H:%M:%S%.3f");
                        println!("  {} {:<25} 0x{:03X}  [{}]",
                            ts, "CAN Frame", frame.id, frame.data_as_hex());
                    }
                }
                Err(knight_rider::can::interface::CanError::Timeout) => continue,
                Err(e) => {
                    total_errors += 1;
                    log::warn!("Receive error: {}", e);
                    std::thread::sleep(Duration::from_millis(100));
                }
            }
        } else {
            // ── Active mode: poll PIDs in round-robin ───────────────────
            if supported_pids.is_empty() {
                std::thread::sleep(Duration::from_millis(500));
                continue;
            }

            let pid = supported_pids[pid_index % supported_pids.len()];
            pid_index += 1;

            // Send request
            let request = ObdRequest::current_data(pid);
            let request_frame = CanFrame::new(request.can_id(), &request.to_can_data());

            if let Err(e) = can.send(&request_frame) {
                total_errors += 1;
                log::warn!("Send failed for {:?}: {}", pid, e);
                std::thread::sleep(poll_interval);
                continue;
            }

            isotp.reset();

            // Wait for response
            let response = wait_for_obd_response(
                &can, &mut isotp, &mut raw_logger,
                Duration::from_millis(300), &mut total_frames,
            );

            match response {
                Some((ecu_id, payload, raw_frame)) => {
                    match ObdResponse::parse(ecu_id, &payload) {
                        Ok(resp) => {
                            match resp.decode(pid) {
                                Ok(decoded) => {
                                    total_decoded += 1;
                                    let ts = Utc::now().format("%H:%M:%S%.3f");
                                    let raw_hex = raw_frame.data_as_hex();

                                    println!("  {} {:<25} {:<10.1} {} {}",
                                        ts, pid.name(),
                                        decoded.value, decoded.unit,
                                        format!("[{}]", raw_hex));

                                    let _ = writeln!(decoded_writer,
                                        "{} | {} | {} | {:.2} {}",
                                        Utc::now().format("%Y-%m-%dT%H:%M:%S%.3fZ"),
                                        pid.name(),
                                        raw_hex,
                                        decoded.value,
                                        decoded.unit,
                                    );
                                }
                                Err(e) => {
                                    total_errors += 1;
                                    log::debug!("Decode error for {:?}: {}", pid, e);
                                }
                            }
                        }
                        Err(e) => {
                            total_errors += 1;
                            log::debug!("Parse error for {:?}: {}", pid, e);
                        }
                    }
                }
                None => {
                    let ts = Utc::now().format("%H:%M:%S%.3f");
                    println!("  {} {:<25} TIMEOUT", ts, pid.name());
                }
            }

            std::thread::sleep(poll_interval);
        }

        // Periodic flush
        if total_frames % 50 == 0 && total_frames > 0 {
            raw_logger.flush()?;
            decoded_writer.flush()?;
        }
    }

    // ── Wrap up ─────────────────────────────────────────────────────────
    raw_logger.flush()?;
    decoded_writer.flush()?;

    let elapsed = start_time.elapsed();

    // Write session summary
    let mut summary = OpenOptions::new()
        .create(true).write(true).truncate(true)
        .open(&summary_path)?;
    writeln!(summary, "Knight Rider OBD-II Logger Session Summary")?;
    writeln!(summary, "===========================================")?;
    writeln!(summary, "Session:       {}", session_ts)?;
    writeln!(summary, "Interface:     {}", config.interface_name)?;
    writeln!(summary, "Mode:          {}", if config.passive { "passive" } else { "active" })?;
    writeln!(summary, "Duration:      {:.1}s", elapsed.as_secs_f64())?;
    writeln!(summary, "Total frames:  {}", total_frames)?;
    writeln!(summary, "Decoded vals:  {}", total_decoded)?;
    writeln!(summary, "Errors:        {}", total_errors)?;
    writeln!(summary, "")?;
    writeln!(summary, "Log files:")?;
    writeln!(summary, "  Raw CSV:     {}", raw_csv_path.display())?;
    writeln!(summary, "  Decoded:     {}", decoded_log_path.display())?;

    println!();
    println!("╔══════════════════════════════════════════════════╗");
    println!("║              Session Complete                    ║");
    println!("╠══════════════════════════════════════════════════╣");
    println!("║  Duration:      {:<33}║", format!("{:.1}s", elapsed.as_secs_f64()));
    println!("║  Total frames:  {:<33}║", total_frames);
    println!("║  Decoded vals:  {:<33}║", total_decoded);
    println!("║  Errors:        {:<33}║", total_errors);
    println!("║                                                  ║");
    println!("║  Logs saved to:                                  ║");
    println!("║    {:<46}║", raw_csv_path.display());
    println!("║    {:<46}║", decoded_log_path.display());
    println!("╚══════════════════════════════════════════════════╝");

    Ok(())
}

// ── Helper functions ────────────────────────────────────────────────────────

/// Discover which PIDs the vehicle supports by querying PID 0x00 and 0x20.
fn discover_supported_pids(
    can: &CanInterface,
    isotp: &mut IsoTpSession,
    logger: &mut TimeseriesLogger,
) -> Vec<ObdPid> {
    let mut supported = Vec::new();

    // Query PID 0x00 (supported PIDs 01-20)
    if let Some(bitmask) = query_pid_support(can, isotp, logger, ObdPid::SupportedPids01To20) {
        let pids = obd::parse_supported_pids(&bitmask);
        log::info!("ECU supports PIDs (01-20): {:02X?}", pids);

        for pid_num in &pids {
            if let Some(pid) = raw_pid_to_enum(*pid_num) {
                supported.push(pid);
            }
        }

        // If PID 0x20 is supported, query the next range
        if pids.contains(&0x20) {
            if let Some(bitmask2) = query_pid_support(can, isotp, logger, ObdPid::SupportedPids21To40) {
                let pids2 = obd::parse_supported_pids(&bitmask2);
                log::info!("ECU supports PIDs (21-40): {:02X?}", pids2);
                for pid_num in &pids2 {
                    if let Some(pid) = raw_pid_to_enum(*pid_num + 0x20) {
                        supported.push(pid);
                    }
                }
            }
        }
    }

    supported
}

fn query_pid_support(
    can: &CanInterface,
    isotp: &mut IsoTpSession,
    logger: &mut TimeseriesLogger,
    support_pid: ObdPid,
) -> Option<Vec<u8>> {
    let request = ObdRequest::current_data(support_pid);
    let frame = CanFrame::new(request.can_id(), &request.to_can_data());

    if can.send(&frame).is_err() {
        return None;
    }

    isotp.reset();
    let timeout = Duration::from_millis(1000);
    let start = Instant::now();

    while start.elapsed() < timeout {
        match can.recv() {
            Ok(frame) => {
                log_raw_frame(&frame, logger);

                if addressing::is_obd_response(frame.id) {
                    if let Ok(Some(payload)) = isotp.receive(frame.data()) {
                        if let Ok(response) = ObdResponse::parse(frame.id, &payload) {
                            return Some(response.data);
                        }
                    }
                }
            }
            Err(knight_rider::can::interface::CanError::Timeout) => continue,
            Err(_) => break,
        }
    }

    None
}

/// Map a raw PID number to our ObdPid enum (if we know it).
fn raw_pid_to_enum(pid: u8) -> Option<ObdPid> {
    match pid {
        0x05 => Some(ObdPid::CoolantTemperature),
        0x0C => Some(ObdPid::EngineRpm),
        0x0D => Some(ObdPid::VehicleSpeed),
        0x0F => Some(ObdPid::IntakeAirTemperature),
        0x11 => Some(ObdPid::ThrottlePosition),
        0x2F => Some(ObdPid::FuelTankLevel),
        _ => None,
    }
}

/// Wait for an OBD-II response, logging all raw frames along the way.
fn wait_for_obd_response(
    can: &CanInterface,
    isotp: &mut IsoTpSession,
    logger: &mut TimeseriesLogger,
    timeout: Duration,
    frame_count: &mut u64,
) -> Option<(u32, Vec<u8>, CanFrame)> {
    let start = Instant::now();

    while start.elapsed() < timeout {
        match can.recv() {
            Ok(frame) => {
                *frame_count += 1;
                log_raw_frame(&frame, logger);

                if addressing::is_obd_response(frame.id) {
                    match isotp.receive(frame.data()) {
                        Ok(Some(payload)) => return Some((frame.id, payload, frame)),
                        Ok(None) => continue,
                        Err(e) => {
                            log::debug!("ISO-TP error: {}", e);
                            return None;
                        }
                    }
                }
            }
            Err(knight_rider::can::interface::CanError::Timeout) => continue,
            Err(e) => {
                log::debug!("CAN error: {}", e);
                return None;
            }
        }
    }

    None
}

/// Try to decode a passive-mode response and log it.
fn try_decode_and_log(
    ecu_id: u32,
    payload: &[u8],
    frame: &CanFrame,
    writer: &mut BufWriter<std::fs::File>,
    decoded_count: &mut u64,
) {
    if let Ok(response) = ObdResponse::parse(ecu_id, payload) {
        for (pid, _name) in POLL_PIDS {
            if response.pid == *pid as u8 {
                if let Ok(decoded) = response.decode(*pid) {
                    *decoded_count += 1;
                    let ts = Utc::now().format("%H:%M:%S%.3f");
                    println!("  {} {:<25} {:<10.1} {} [{}]",
                        ts, pid.name(), decoded.value, decoded.unit, frame.data_as_hex());

                    let _ = writeln!(writer,
                        "{} | {} | {} | {:.2} {}",
                        Utc::now().format("%Y-%m-%dT%H:%M:%S%.3fZ"),
                        pid.name(), frame.data_as_hex(),
                        decoded.value, decoded.unit,
                    );
                    return;
                }
            }
        }

        // Unknown PID - still log it
        let ts = Utc::now().format("%H:%M:%S%.3f");
        println!("  {} {:<25} PID 0x{:02X}  [{}]",
            ts, "Unknown OBD Response", response.pid, frame.data_as_hex());
    }
}

/// Log a raw CAN frame to the CSV logger.
fn log_raw_frame(frame: &CanFrame, logger: &mut TimeseriesLogger) {
    let entry = RawFrameEntry {
        timestamp: Utc::now(),
        can_id: frame.id,
        dlc: frame.dlc,
        data: frame.data,
    };
    let _ = logger.log_frame(&entry);
}

// ── Hardware Diagnostics ────────────────────────────────────────────────────

/// Run hardware diagnostics to verify CAN HAT is connected and working.
fn run_diagnostics(interface_name: &str) {
    println!();
    println!("╔══════════════════════════════════════════════════╗");
    println!("║   Knight Rider - Hardware Diagnostics            ║");
    println!("╠══════════════════════════════════════════════════╣");
    println!("║  Checking CAN HAT, SPI, power, and interface    ║");
    println!("╚══════════════════════════════════════════════════╝");
    println!();

    let mut pass_count = 0;
    let mut fail_count = 0;
    let mut warn_count = 0;

    // ── Check 1: Platform ───────────────────────────────────────────────
    print!("  [1/7] Platform .............. ");
    #[cfg(target_os = "linux")]
    {
        println!("✓ Linux (aarch64)");
        pass_count += 1;
    }
    #[cfg(not(target_os = "linux"))]
    {
        println!("✗ Not Linux — CAN HAT requires Linux");
        fail_count += 1;
    }

    #[cfg(target_os = "linux")]
    {
        // ── Check 2: Board model ────────────────────────────────────────
        print!("  [2/7] Board ................. ");
        match std::fs::read_to_string("/proc/device-tree/model") {
            Ok(model) => {
                let model = model.trim_end_matches('\0');
                if model.contains("Raspberry Pi") {
                    println!("✓ {}", model);
                    pass_count += 1;
                } else {
                    println!("⚠ {} (expected Raspberry Pi)", model);
                    warn_count += 1;
                }
            }
            Err(_) => {
                println!("⚠ Cannot detect board model");
                warn_count += 1;
            }
        }

        // ── Check 3: SPI enabled ────────────────────────────────────────
        print!("  [3/7] SPI bus ............... ");
        let spi_exists = std::path::Path::new("/dev/spidev0.0").exists()
            || std::path::Path::new("/dev/spidev0.1").exists();
        if spi_exists {
            println!("✓ SPI devices found");
            pass_count += 1;
        } else {
            println!("✗ No SPI devices — add 'dtparam=spi=on' to /boot/firmware/config.txt");
            fail_count += 1;
        }

        // ── Check 4: CAN kernel module ──────────────────────────────────
        print!("  [4/7] CAN kernel module ..... ");
        let dmesg = run_cmd("dmesg");
        if dmesg.contains("CAN device driver interface") {
            println!("✓ CAN driver loaded");
            pass_count += 1;
        } else {
            println!("✗ CAN driver not loaded");
            fail_count += 1;
        }

        // ── Check 5: MCP2515 probe ──────────────────────────────────────
        print!("  [5/7] MCP2515 CAN chip ...... ");
        if dmesg.contains("mcp251x") || dmesg.contains("MCP251") {
            if dmesg.contains("Probe failed") || dmesg.contains("didn't enter in conf mode") {
                println!("✗ MCP2515 detected but FAILED to initialize");
                println!("        Possible causes:");
                println!("        - HAT not seated firmly on GPIO pins");
                println!("        - Wrong oscillator frequency in config.txt");
                println!("        - Insufficient power supply (need 5V/5A)");
                // Show the actual error
                for line in dmesg.lines() {
                    if line.contains("mcp251x") && (line.contains("Probe") || line.contains("didn't enter")) {
                        println!("        dmesg: {}", line.trim());
                    }
                }
                fail_count += 1;
            } else {
                println!("✓ MCP2515 initialized successfully");
                pass_count += 1;
            }
        } else {
            println!("✗ MCP2515 not detected — is the HAT connected to GPIO?");
            println!("        Check /boot/firmware/config.txt has:");
            println!("        dtoverlay=mcp2515-can0,oscillator=FREQ,interrupt=25");
            fail_count += 1;
        }

        // ── Check 6: CAN interface ──────────────────────────────────────
        print!("  [6/7] CAN interface ({}) .. ", interface_name);
        let ip_output = run_cmd(&format!("ip link show {}", interface_name));
        if ip_output.contains(interface_name) {
            if ip_output.contains("UP") {
                println!("✓ {} is UP", interface_name);
                pass_count += 1;
            } else {
                println!("⚠ {} exists but is DOWN — run: sudo ip link set {} up", interface_name, interface_name);
                warn_count += 1;
            }
        } else {
            println!("✗ {} does not exist", interface_name);
            fail_count += 1;
        }

        // ── Check 7: Power supply ───────────────────────────────────────
        print!("  [7/7] Power supply .......... ");
        let throttled = run_cmd("vcgencmd get_throttled");
        if throttled.contains("0x0") {
            println!("✓ No undervoltage detected");
            pass_count += 1;
        } else if throttled.contains("throttled=0x") {
            let hex = throttled.trim().replace("throttled=", "");
            println!("⚠ Throttling flags: {}", hex);
            if throttled.contains("0x50000") || throttled.contains("0x50005") {
                println!("        ⚡ UNDERVOLTAGE detected! Power supply is too weak.");
                println!("        Use the official Pi 5 PSU (5V/5A, 27W).");
            }
            warn_count += 1;
        } else {
            println!("⚠ Cannot check ({})", throttled.trim());
            warn_count += 1;
        }
    }

    // ── Summary ─────────────────────────────────────────────────────────
    println!();
    println!("  ──────────────────────────────────────────────────");
    println!("  Results: {} passed, {} warnings, {} failed", pass_count, warn_count, fail_count);
    println!();

    if fail_count == 0 && warn_count == 0 {
        println!("  ✅ ALL CHECKS PASSED — CAN HAT is ready!");
        println!("     Connect OBD-II cable to car and run:");
        println!("     sudo obd-logger -d 60");
    } else if fail_count == 0 {
        println!("  ⚠  PASSED WITH WARNINGS — review items above");
    } else {
        println!("  ❌ {} CHECK(S) FAILED — fix the issues above before connecting to car", fail_count);
    }
    println!();
}

/// Run a shell command and return stdout.
#[cfg(target_os = "linux")]
fn run_cmd(cmd: &str) -> String {
    std::process::Command::new("sh")
        .arg("-c")
        .arg(cmd)
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
        .unwrap_or_default()
}

