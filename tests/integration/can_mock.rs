//! Integration tests using virtual CAN (vcan0).
//!
//! These tests require Linux with SocketCAN support.
//! Run: sudo ./scripts/setup-vcan.sh first.

#[cfg(target_os = "linux")]
mod linux_tests {
    // Integration tests that require vcan0
    // These would spawn a mock ECU thread that responds to requests
    
    #[test]
    #[ignore] // Requires vcan0 setup
    fn test_rpm_request_response() {
        // TODO: Implement with vcan0
        // 1. Open vcan0
        // 2. Spawn thread to send mock response
        // 3. Send request
        // 4. Verify response decoding
    }
}

// Tests that work on all platforms
mod unit_tests {
    use knight_rider::can::{IsoTpSession, ObdPid, ObdRequest, ObdResponse};
    use knight_rider::can::obd::{addressing, parse_supported_pids};

    #[test]
    fn test_obd_request_encoding() {
        let request = ObdRequest::current_data(ObdPid::EngineRpm);
        let data = request.to_can_data();
        
        assert_eq!(data[0], 0x02); // Length
        assert_eq!(data[1], 0x01); // Mode 01
        assert_eq!(data[2], 0x0C); // PID 0C (RPM)
        assert_eq!(request.can_id(), 0x7DF);
    }

    #[test]
    fn test_rpm_decoding() {
        // 3000 RPM = 0x2EE0 raw = ((0x2E * 256) + 0xE0) / 4
        let response = ObdResponse::parse(0x7E8, &[0x41, 0x0C, 0x2E, 0xE0]).unwrap();
        let decoded = response.decode(ObdPid::EngineRpm).unwrap();
        
        assert_eq!(decoded.value, 3000.0);
        assert_eq!(decoded.unit, "rpm");
    }

    #[test]
    fn test_speed_decoding() {
        // 100 km/h = 0x64
        let response = ObdResponse::parse(0x7E8, &[0x41, 0x0D, 0x64]).unwrap();
        let decoded = response.decode(ObdPid::VehicleSpeed).unwrap();
        
        assert_eq!(decoded.value, 100.0);
        assert_eq!(decoded.unit, "km/h");
    }

    #[test]
    fn test_temperature_decoding() {
        // 90°C = 130 raw (130 - 40 = 90)
        let response = ObdResponse::parse(0x7E8, &[0x41, 0x05, 0x82]).unwrap();
        let decoded = response.decode(ObdPid::CoolantTemperature).unwrap();
        
        assert_eq!(decoded.value, 90.0);
        assert_eq!(decoded.unit, "°C");
    }

    #[test]
    fn test_throttle_decoding() {
        // ~50% = 128 raw = (128 * 100) / 255
        let response = ObdResponse::parse(0x7E8, &[0x41, 0x11, 0x80]).unwrap();
        let decoded = response.decode(ObdPid::ThrottlePosition).unwrap();
        
        assert!((decoded.value - 50.2).abs() < 0.1);
        assert_eq!(decoded.unit, "%");
    }

    #[test]
    fn test_supported_pids_parsing() {
        let data = [0xBE, 0x1F, 0xB8, 0x10];
        let supported = parse_supported_pids(&data);
        
        assert!(supported.contains(&5));  // Coolant temp
        assert!(supported.contains(&12)); // RPM
        assert!(supported.contains(&13)); // Speed
    }

    #[test]
    fn test_isotp_single_frame() {
        let mut session = IsoTpSession::new();
        
        // Single frame: 04 41 0C 2E E0 00 00 00
        let data = [0x04, 0x41, 0x0C, 0x2E, 0xE0, 0x00, 0x00, 0x00];
        let result = session.receive(&data).unwrap();
        
        assert!(result.is_some());
        assert_eq!(result.unwrap(), vec![0x41, 0x0C, 0x2E, 0xE0]);
    }

    #[test]
    fn test_isotp_multiframe() {
        let mut session = IsoTpSession::new();
        
        // First frame: 10 bytes total
        let ff = [0x10, 0x0A, 0x41, 0x00, 0xBE, 0x1F, 0xB8, 0x10];
        assert!(session.receive(&ff).unwrap().is_none());
        
        // Consecutive frame 1
        let cf = [0x21, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00];
        let result = session.receive(&cf).unwrap();
        
        assert!(result.is_some());
        assert_eq!(result.unwrap().len(), 10);
    }

    #[test]
    fn test_negative_response() {
        // 7F 01 11 = Negative response, service 01, error 11 (service not supported)
        let result = ObdResponse::parse(0x7E8, &[0x7F, 0x01, 0x11]);
        
        assert!(result.is_err());
    }

    #[test]
    fn test_obd_response_validation() {
        let request = ObdRequest::current_data(ObdPid::EngineRpm);
        let response = ObdResponse::parse(0x7E8, &[0x41, 0x0C, 0x2E, 0xE0]).unwrap();
        
        assert!(response.validate(&request).is_ok());
    }

    #[test]
    fn test_obd_response_id_check() {
        assert!(addressing::is_obd_response(0x7E8));
        assert!(addressing::is_obd_response(0x7EF));
        assert!(!addressing::is_obd_response(0x7DF));
        assert!(!addressing::is_obd_response(0x100));
    }
}
