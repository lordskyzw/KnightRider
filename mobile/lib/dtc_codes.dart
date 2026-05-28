/// Lookup table for the most common generic OBD-II diagnostic trouble codes.
///
/// Covers the codes a workshop is statistically most likely to see on a
/// Toyota/Honda/Nissan/Mazda in Zimbabwe — fuel trim, misfires, evap,
/// catalyst, O2 sensors, idle-control. Manufacturer-specific P1xxx codes
/// vary too widely to encode here; those just show as the bare code.
const Map<String, String> dtcDescriptions = {
  // ── Fuel & air metering (P00xx, P01xx) ─────────────────────────────────
  'p0010': 'A camshaft position actuator circuit (bank 1)',
  'p0011': 'A camshaft position - timing over-advanced (bank 1)',
  'p0012': 'A camshaft position - timing over-retarded (bank 1)',
  'p0100': 'Mass air flow circuit malfunction',
  'p0101': 'Mass air flow circuit range/performance',
  'p0102': 'Mass air flow circuit low input',
  'p0103': 'Mass air flow circuit high input',
  'p0106': 'MAP/baro circuit range/performance',
  'p0107': 'MAP/baro circuit low input',
  'p0108': 'MAP/baro circuit high input',
  'p0110': 'Intake air temperature circuit malfunction',
  'p0111': 'Intake air temperature circuit range/performance',
  'p0112': 'Intake air temperature circuit low input',
  'p0113': 'Intake air temperature circuit high input',
  'p0115': 'Engine coolant temperature circuit malfunction',
  'p0116': 'Engine coolant temperature range/performance',
  'p0117': 'Engine coolant temperature circuit low input',
  'p0118': 'Engine coolant temperature circuit high input',
  'p0120': 'Throttle/pedal position sensor A circuit',
  'p0121': 'Throttle/pedal position sensor A range/performance',
  'p0122': 'Throttle/pedal position sensor A low input',
  'p0123': 'Throttle/pedal position sensor A high input',
  'p0125': 'Insufficient coolant temp for closed-loop fuel control',
  'p0128': 'Coolant thermostat below regulating temperature',
  'p0130': 'O2 sensor circuit malfunction (B1S1)',
  'p0131': 'O2 sensor circuit low voltage (B1S1)',
  'p0132': 'O2 sensor circuit high voltage (B1S1)',
  'p0133': 'O2 sensor circuit slow response (B1S1)',
  'p0134': 'O2 sensor circuit no activity (B1S1)',
  'p0135': 'O2 sensor heater circuit malfunction (B1S1)',
  'p0136': 'O2 sensor circuit malfunction (B1S2)',
  'p0137': 'O2 sensor circuit low voltage (B1S2)',
  'p0138': 'O2 sensor circuit high voltage (B1S2)',
  'p0140': 'O2 sensor circuit no activity (B1S2)',
  'p0141': 'O2 sensor heater circuit malfunction (B1S2)',
  'p0150': 'O2 sensor circuit malfunction (B2S1)',
  'p0151': 'O2 sensor circuit low voltage (B2S1)',
  'p0155': 'O2 sensor heater circuit (B2S1)',
  'p0156': 'O2 sensor circuit malfunction (B2S2)',
  'p0161': 'O2 sensor heater circuit (B2S2)',
  'p0170': 'Fuel trim malfunction (bank 1)',
  'p0171': 'System too lean (bank 1)',
  'p0172': 'System too rich (bank 1)',
  'p0173': 'Fuel trim malfunction (bank 2)',
  'p0174': 'System too lean (bank 2)',
  'p0175': 'System too rich (bank 2)',
  'p0190': 'Fuel rail pressure sensor circuit',
  'p0191': 'Fuel rail pressure sensor circuit range/performance',

  // ── Misfires (P03xx) ───────────────────────────────────────────────────
  'p0300': 'Random/multiple cylinder misfire detected',
  'p0301': 'Cylinder 1 misfire detected',
  'p0302': 'Cylinder 2 misfire detected',
  'p0303': 'Cylinder 3 misfire detected',
  'p0304': 'Cylinder 4 misfire detected',
  'p0305': 'Cylinder 5 misfire detected',
  'p0306': 'Cylinder 6 misfire detected',
  'p0307': 'Cylinder 7 misfire detected',
  'p0308': 'Cylinder 8 misfire detected',
  'p0325': 'Knock sensor 1 circuit malfunction',
  'p0335': 'Crankshaft position sensor A circuit malfunction',
  'p0336': 'Crankshaft position sensor range/performance',
  'p0340': 'Camshaft position sensor circuit malfunction',
  'p0341': 'Camshaft position sensor range/performance',
  'p0350': 'Ignition coil primary/secondary circuit',
  'p0351': 'Ignition coil A primary/secondary circuit',
  'p0352': 'Ignition coil B primary/secondary circuit',
  'p0353': 'Ignition coil C primary/secondary circuit',
  'p0354': 'Ignition coil D primary/secondary circuit',

  // ── Emissions & EGR (P04xx) ────────────────────────────────────────────
  'p0401': 'EGR flow insufficient',
  'p0402': 'EGR flow excessive',
  'p0403': 'EGR control circuit',
  'p0404': 'EGR control circuit range/performance',
  'p0411': 'Secondary air injection system - incorrect flow',
  'p0420': 'Catalyst system efficiency below threshold (bank 1)',
  'p0421': 'Warm-up catalyst efficiency below threshold (bank 1)',
  'p0430': 'Catalyst system efficiency below threshold (bank 2)',
  'p0440': 'Evaporative emission system malfunction',
  'p0441': 'Evap emission system incorrect purge flow',
  'p0442': 'Evap emission system small leak detected',
  'p0443': 'Evap emission system purge control valve',
  'p0446': 'Evap emission system vent control circuit',
  'p0455': 'Evap emission system large leak detected',
  'p0456': 'Evap emission system very small leak detected',

  // ── Vehicle speed, idle, AT (P05xx) ────────────────────────────────────
  'p0500': 'Vehicle speed sensor malfunction',
  'p0501': 'Vehicle speed sensor range/performance',
  'p0505': 'Idle air control system malfunction',
  'p0506': 'Idle air control system - RPM lower than expected',
  'p0507': 'Idle air control system - RPM higher than expected',
  'p0562': 'System voltage low',
  'p0563': 'System voltage high',

  // ── Computer / output (P06xx) ──────────────────────────────────────────
  'p0601': 'Internal control module memory checksum error',
  'p0606': 'PCM processor fault',
  'p0700': 'Transmission control system malfunction',
  'p0715': 'Input/turbine speed sensor circuit',
  'p0720': 'Output speed sensor circuit',
  'p0725': 'Engine speed input circuit',

  // ── Network / CAN (U codes) ────────────────────────────────────────────
  'u0073': 'Control module communication bus A off',
  'u0100': 'Lost communication with ECM/PCM',
  'u0101': 'Lost communication with TCM',
  'u0121': 'Lost communication with ABS module',
  'u0140': 'Lost communication with body control module',
  'u0155': 'Lost communication with instrument cluster',
};

/// Best-effort description for a DTC signal name (`dtc.stored.p0301` style).
///
/// Returns `null` if the code isn't in our lookup — callers should fall
/// back to the bare code text in that case.
String? describeDtc(String signalName) {
  // Signals look like: `dtc.stored.p0301`, `dtc.cleared.u0073`.
  final i = signalName.lastIndexOf('.');
  if (i < 0) return null;
  final code = signalName.substring(i + 1).toLowerCase();
  return dtcDescriptions[code];
}

/// Severity bucket for colour-coding. Misfires + catalyst are high; sensor
/// drift is medium; evap small-leak is low; communication is medium.
DtcSeverity dtcSeverity(String signalName) {
  final i = signalName.lastIndexOf('.');
  if (i < 0) return DtcSeverity.unknown;
  final code = signalName.substring(i + 1).toLowerCase();

  // P03xx misfires, P0420/P0430 catalyst, P0606 PCM
  if (code.startsWith('p030') ||
      code == 'p0420' || code == 'p0421' || code == 'p0430' ||
      code == 'p0606' || code == 'p0601') {
    return DtcSeverity.high;
  }
  // System voltage extremes
  if (code == 'p0562' || code == 'p0563') return DtcSeverity.high;
  // Lean/rich
  if (code == 'p0171' || code == 'p0172' || code == 'p0174' || code == 'p0175') {
    return DtcSeverity.high;
  }
  // Evap small/very-small leak — common nuisance code
  if (code == 'p0442' || code == 'p0455' || code == 'p0456') {
    return DtcSeverity.low;
  }
  // Network comms
  if (code.startsWith('u0')) return DtcSeverity.medium;
  return DtcSeverity.medium;
}

enum DtcSeverity { unknown, low, medium, high }
