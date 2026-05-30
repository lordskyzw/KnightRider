/// The set of vehicles the dashboard can render, and which 3D/silhouette art
/// each one ships with. This is the single source of truth for "which car";
/// the dashboard and Settings picker both read it.
///
/// Adding a car is drop-in: put `assets/cars/<id>.glb` in the bundle (the
/// pubspec already globs `assets/cars/`) and add a [VehicleModel] entry here.
/// VIN can't pick the car for us — VIN isn't reliably OBD-readable in the field
/// (Honda blocks it, the Toyota Axio doesn't expose Mode 09 PID 02), so the
/// user chooses in Settings. See the obd-vehicle-id-constraint note.
/// Per-vehicle map from semantic slots to GLB material-name substrings (matched
/// case-insensitively). Each GLB names its materials differently, so the
/// recolor/lamp logic can't hardcode names — the dashboard injects the right
/// map for the selected car. Empty list = that slot isn't separable on this
/// model (no effect, no crash).
class MaterialMap {
  final List<String> body;   // body paint (recolorable)
  final List<String> wheel;  // wheels/tyres (recolorable)
  final List<String> head;   // head/low-beam/fog/DRL + general lamp lenses
  final List<String> tail;   // taillights (brake-reactive)
  final List<String> signalL; // left indicator
  final List<String> signalR; // right indicator
  final List<String> reverse; // reverse lamp
  const MaterialMap({
    this.body = const [],
    this.wheel = const [],
    this.head = const [],
    this.tail = const [],
    this.signalL = const [],
    this.signalR = const [],
    this.reverse = const [],
  });
}

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
  /// Material-name map for recolour/lamps in this specific GLB.
  final MaterialMap materials;
  /// Sign of the model's "front" along Z (+1 or −1). GLBs face different ways;
  /// this orients the anatomical hotspots (engine front, fuel rear). The Vitz/
  /// Axio are +1 (field-verified: engine node lands at the front). Tune per car.
  final double frontZ;

  const VehicleModel({
    required this.id,
    required this.name,
    required this.glbAsset,
    required this.silhouetteAsset,
    required this.credit,
    this.materials = const MaterialMap(),
    this.frontZ = 1.0,
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
    // The Vitz GLB has cleanly-named per-lamp materials.
    materials: MaterialMap(
      body: ['paint'],
      wheel: ['tire'],
      head: ['lowbeam', 'foglight', 'vehiclelights'],
      tail: ['tail'],
      signalL: ['signal_l'],
      signalR: ['signal_r'],
      reverse: ['reverse'],
    ),
  ),
  VehicleModel(
    id: 'axio',
    name: 'Toyota Corolla Axio (E160)',
    glbAsset: 'assets/cars/axio.glb',
    silhouetteAsset: _kGenericSilhouette,
    credit: 'Corolla Axio by taeemtasbi · CC BY 4.0',
    // This GLB isn't authored with per-lamp materials: body/wheel recolour are
    // clean (main_paint / tyre_side), but the only lamp material is the shared
    // lens 'klosz', so 'head' lights all lenses together and there's no
    // separable tail/turn/reverse. Body recolour also tints the rims (they
    // share main_paint).
    materials: MaterialMap(
      body: ['main_paint'],
      wheel: ['tyre_side'],
      head: ['klosz'],
    ),
  ),
  VehicleModel(
    id: 'gle',
    name: 'Mercedes-Benz GLE63 AMG Coupé',
    glbAsset: 'assets/cars/gle.glb',
    silhouetteAsset: _kGenericSilhouette,
    credit: 'GLE63 AMG Coupé by Ddiaz Design · CC BY-NC-SA 4.0',
    // Cleanly named materials: paint, tyre/rim, lamp lenses split head vs tail.
    materials: MaterialMap(
      body: ['carpaint'],
      wheel: ['rim', 'disk'],
      head: ['light'], // glass_light / light_ref / light_map
      tail: ['red_glass'],
    ),
  ),
  VehicleModel(
    id: 'gwagon',
    name: 'Mercedes-Benz G-Class (W463)',
    glbAsset: 'assets/cars/gwagon.glb',
    silhouetteAsset: _kGenericSilhouette,
    credit: 'G-Class by Lexyc16 · CC BY 4.0',
    // GLB uses generic Material.00x names — not separable, so no recolour/lamps.
    // Renders in its factory black, which looks right as-is.
    materials: MaterialMap(),
  ),
  VehicleModel(
    id: 'eclass',
    name: 'Mercedes-Benz E-Class (W212)',
    glbAsset: 'assets/cars/eclass.glb',
    silhouetteAsset: _kGenericSilhouette,
    credit: 'E-Class W212 by Black Snow · CC BY 4.0',
    materials: MaterialMap(
      body: ['body_color'],
      wheel: ['wheel'],
      head: ['projector_light'],
      tail: ['taillight'],
    ),
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
