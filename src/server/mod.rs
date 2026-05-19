//! Local HTTP + WebSocket server.
//!
//! Bound to `0.0.0.0:8080` on the Pi LAN. No TLS, no auth — open during demo
//! phase (see [[knight-rider-system-architecture]] memory).
//!
//! Routes:
//!
//! | Path                   | Verb | Purpose                                            |
//! |------------------------|------|----------------------------------------------------|
//! | `/health`              | GET  | Liveness probe                                     |
//! | `/ws/live`             | GET  | WebSocket upgrade; streams [`Sample`] JSON         |
//! | `/backlog?since=N`     | GET  | Pi-signed batches with `batch_id > N`              |
//! | `/known-track/alerts`  | GET  | Recent alerts from on-Pi known-track inference     |
//! | `/inbox`               | POST | Cloud→Pi delivery from courier phones              |

// Filled in by tasks #7-#9.
