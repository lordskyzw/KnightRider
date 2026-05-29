/// The set of vehicles the dashboard can render, and which 3D/silhouette art
/// each one ships with. This is the single source of truth for "which car";
/// the dashboard and Settings picker both read it.
///
/// Adding a car is drop-in: put `assets/cars/<id>.glb` in the bundle (the
/// pubspec already globs `assets/cars/`) and add a [VehicleModel] entry here.
/// VIN can't pick the car for us — VIN isn't reliably OBD-readable in the field
/// (Honda blocks it, the Toyota Axio doesn't expose Mode 09 PID 02), so the
/// user chooses in Settings. See the obd-vehicle-id-constraint note.
class VehicleModel {
  final String id; // stable key persisted in prefs (e.g. 'vitz')
  final String name; // shown in the picker
  /// Bundled glTF/GLB asset, or null if we don't have a 3D model for this car
  /// yet — in which case the dashboard falls back to [silhouetteAsset].
  final String? glbAsset;
  /// Flat top-down SVG fallback (used when 3D is off, or no [glbAsset] yet).
  final String silhouetteAsset;
  /// CC-BY attribution, required to stay visible wherever [glbAsset] is shown.
  final String? credit;

  const VehicleModel({
    required this.id,
    required this.name,
    required this.glbAsset,
    required this.silhouetteAsset,
    required this.credit,
  });

  bool get has3d => glbAsset != null;
}

/// Generic top-down silhouette reused until a car has its own SVG. It's a Vitz
/// outline, so it's a placeholder for other cars — the dashboard captions it
/// "3D model coming soon" so it never misrepresents.
const String _kGenericSilhouette = 'assets/cars/vitz.svg';

const List<VehicleModel> kVehicles = [
  VehicleModel(
    id: 'vitz',
    name: 'Toyota Vitz (NSP130)',
    glbAsset: 'assets/cars/vitz.glb',
    silhouetteAsset: 'assets/cars/vitz.svg',
    credit: 'Vitz by Driving501 · CC BY 4.0',
  ),
  VehicleModel(
    id: 'axio',
    name: 'Toyota Corolla Axio (E160)',
    // No axio.glb bundled yet — drop one in assets/cars/ + set this to wire it.
    glbAsset: null,
    silhouetteAsset: _kGenericSilhouette,
    credit: null,
  ),
];

const String kDefaultVehicleId = 'vitz';

/// Look up a vehicle by its persisted id, falling back to the default.
VehicleModel vehicleById(String? id) {
  for (final v in kVehicles) {
    if (v.id == id) return v;
  }
  return kVehicles.firstWhere((v) => v.id == kDefaultVehicleId);
}
