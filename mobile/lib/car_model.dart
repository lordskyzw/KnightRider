import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:model_viewer_plus/model_viewer_plus.dart';

/// The glTF/GLB asset shown in the dashboard centre when the 3D feature flag
/// is on.
///
/// Currently a real photogrammetry Toyota Vitz. Later this becomes a
/// per-vehicle GLB delivered from the cloud on vehicle onboarding; when that
/// lands, only this constant (and the attribution) changes — everything
/// downstream keys off it.
const String kVehicleModelAsset = 'assets/cars/vitz.glb';

/// CC-BY attribution required by the model's licence. Must stay visible
/// wherever the model is shown. Source:
/// https://sketchfab.com/3d-models/toyota-vitz-0d1428782a5f4cf69efd8a15744e1d49
const String kVehicleModelCredit = 'Vitz by Driving501 · CC BY 4.0';

/// A rotatable 3D vehicle model rendered with Google's `<model-viewer>`
/// (PBR + image-based lighting) inside a transparent WebView, so it floats on
/// the dashboard with the RPM glow showing through behind it.
///
/// [tint] recolours the car body. The Vitz GLB has 37 named materials; only
/// the one called `Paint` is the exterior body, so we recolour just that and
/// leave glass, lights, wheels, grille and interior untouched. `null` keeps
/// the factory finish.
class CarModel3D extends StatelessWidget {
  final String src;
  final String alt;
  final Color? tint;

  const CarModel3D({
    super.key,
    this.src = kVehicleModelAsset,
    this.alt = 'Vehicle 3D model',
    this.tint,
  });

  /// JS injected after the model loads to multiply every material's base
  /// colour by [tint]. sRGB→linear converted so the paint reads true.
  String? _tintJs() {
    final t = tint;
    if (t == null) return null;
    double lin(double s) =>
        s <= 0.04045 ? s / 12.92 : math.pow((s + 0.055) / 1.055, 2.4).toDouble();
    final r = lin(t.r), g = lin(t.g), b = lin(t.b);
    return '''
const mv = document.querySelector('model-viewer');
function applyTint() {
  if (!mv || !mv.model) return;
  const c = [$r, $g, $b, 1];
  for (const m of mv.model.materials) {
    // Only the body paint — leave glass, lights, wheels, grille, interior.
    if (m.name && m.name.toLowerCase().includes('paint')) {
      try { m.pbrMetallicRoughness.setBaseColorFactor(c); } catch (e) {}
    }
  }
}
mv.addEventListener('load', applyTint);
applyTint();
''';
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        // Sits behind the (transparent) viewer during the WebGL warm-up so
        // the centre isn't blank; the car covers it once the model paints.
        const Center(
          child: SizedBox(
            width: 26,
            height: 26,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              color: Color(0xFF5A5A5E),
            ),
          ),
        ),
        Positioned.fill(child: _viewer()),
        // CC-BY attribution — must remain visible while the model shows.
        Positioned(
          left: 8,
          bottom: 6,
          child: Text(
            kVehicleModelCredit,
            style: const TextStyle(
              fontSize: 9,
              color: Color(0xFF5A5A5E),
              letterSpacing: 0.2,
            ),
          ),
        ),
      ],
    );
  }

  Widget _viewer() {
    return ModelViewer(
      // Re-create the viewer when the asset OR tint changes so the new paint
      // is applied from a clean load.
      key: ValueKey('$src|${tint?.toARGB32()}'),
      src: src,
      alt: alt,
      // Transparent: no opaque rectangle "framing" the car — the dashboard
      // and RPM glow show through behind it.
      backgroundColor: Colors.transparent,
      relatedJs: _tintJs(),

      // ── Lighting & material quality ──────────────────────────────────
      environmentImage: 'neutral',
      exposure: 1.05,
      shadowIntensity: 0.7,
      shadowSoftness: 1.0,

      // ── Camera & motion ──────────────────────────────────────────────
      // No auto-rotate — the car holds the hero angle and the user can still
      // drag to orbit it manually (cameraControls).
      cameraControls: true,
      disableZoom: false,
      autoRotate: false,
      cameraOrbit: '28deg 74deg 98%',
      minCameraOrbit: 'auto 55deg auto',
      maxCameraOrbit: 'auto 88deg auto',
      fieldOfView: '30deg',
      interpolationDecay: 220,

      ar: false,
      interactionPrompt: InteractionPrompt.none,
      loading: Loading.eager,
    );
  }
}
