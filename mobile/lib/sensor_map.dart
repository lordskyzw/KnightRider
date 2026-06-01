/// The "anatomy" of the car's telemetry: where each signal physically lives.
///
/// Positions are normalised to the model's bounding box — `n*` in [-1, 1] as a
/// fraction of each half-extent, in a canonical orientation (x = right,
/// y = up, z = front). The dashboard's 3D view converts these to real model
/// coordinates from the loaded GLB's bounding box, so the same anatomy maps
/// onto any car. Per-vehicle orientation differences are handled by a frontZ
/// sign in the viewer (tuned on-device).
///
/// This is vehicle-independent — it describes the data surface, not a specific
/// GLB. Status (live / available / fault) is computed by the dashboard from the
/// live sample map and pushed into the viewer to colour each glowing node.
class SensorNode {
  final String id;
  final String label;
  final String where; // human-readable physical location
  final List<String> signals; // signal keys this node surfaces
  final List<String> faultKeys; // dtc.* keys that turn this node red
  final double nx, ny, nz; // normalised position in the bounding box

  const SensorNode({
    required this.id,
    required this.label,
    required this.where,
    required this.signals,
    this.faultKeys = const [],
    required this.nx,
    required this.ny,
    required this.nz,
  });
}

const List<SensorNode> kSensorNodes = [
  SensorNode(
    id: 'engine',
    label: 'Engine',
    where: 'Engine bay — front',
    signals: [
      'obd.rpm', 'dbc.toyota.POWERTRAIN.engine_rpm', 'obd.engine_load',
      'obd.abs_load', 'obd.timing_advance', 'obd.stft_b1', 'obd.ltft_b1',
    ],
    nx: 0.0, ny: 0.18, nz: 0.62,
  ),
  SensorNode(
    id: 'intake',
    label: 'Air intake',
    where: 'Intake manifold — front',
    signals: [
      'obd.maf', 'obd.map', 'obd.intake_air_temp', 'obd.throttle',
      'obd.commanded_lambda',
    ],
    nx: 0.30, ny: 0.34, nz: 0.42,
  ),
  SensorNode(
    id: 'coolant',
    label: 'Cooling',
    where: 'Thermostat — front',
    signals: ['obd.coolant_temp'],
    nx: -0.24, ny: 0.02, nz: 0.66,
  ),
  SensorNode(
    id: 'battery',
    label: 'Battery & charging',
    where: 'Battery — front corner',
    signals: ['obd.battery_v'],
    nx: -0.5, ny: 0.22, nz: 0.5,
  ),
  SensorNode(
    id: 'exhaust',
    label: 'Exhaust & catalyst',
    where: 'Catalytic converter & O₂ sensor — underbody',
    signals: [
      'obd.cat_temp_b1s1', 'obd.cat_temp_b1s2',
      'obd.o2s1_eq_ratio', 'obd.o2_b1s2_v',
      'obd.commanded_lambda',
    ],
    faultKeys: ['dtc.stored.p0420', 'dtc.stored.p0430'],
    nx: 0.0, ny: -0.55, nz: -0.1,
  ),
  SensorNode(
    id: 'fuel',
    label: 'Fuel',
    where: 'Fuel tank — rear',
    signals: ['obd.fuel_level'],
    nx: 0.0, ny: -0.15, nz: -0.62,
  ),
  SensorNode(
    id: 'speed',
    label: 'Speed',
    where: 'Wheel speed / transmission',
    signals: ['obd.speed', 'dbc.toyota.SPEED.speed'],
    nx: 0.5, ny: -0.5, nz: -0.35,
  ),
  SensorNode(
    id: 'brake',
    label: 'Brakes',
    where: 'Brake system',
    signals: ['dbc.toyota.BRAKE.pressed', 'dbc.toyota.STOP_LAMP.on'],
    nx: -0.5, ny: -0.5, nz: -0.35,
  ),
  SensorNode(
    id: 'door',
    label: 'Doors',
    where: 'Driver door',
    signals: ['dbc.toyota.DOORS.driver'],
    nx: -0.72, ny: 0.05, nz: 0.05,
  ),
  SensorNode(
    id: 'ambient',
    label: 'Ambient & DTCs',
    where: 'Air / barometric · stored fault codes',
    signals: ['obd.baro_pressure', 'obd.ambient_air_temp', 'obd.dtc_count'],
    nx: 0.0, ny: 0.46, nz: 0.0,
  ),
];

/// Sensor-node connection/health state, used to colour the glowing hotspot.
enum SensorStatus { live, available, fault }

extension SensorStatusName on SensorStatus {
  String get wire => switch (this) {
        SensorStatus.live => 'live',
        SensorStatus.available => 'available',
        SensorStatus.fault => 'fault',
      };
}
