import 'package:flutter/material.dart';
import 'package:model_viewer_plus/model_viewer_plus.dart';

/// The glTF/GLB asset shown in the dashboard centre when the 3D feature flag
/// is on.
///
/// PLACEHOLDER: this is the Khronos "ToyCar" model (CC0, public domain) used
/// to prove the renderer, lighting and orbit feel. The production asset is a
/// real per-vehicle GLB (starting with the Vitz NSP130), delivered from the
/// cloud on vehicle onboarding. When that lands, only this one constant
/// changes — everything downstream keys off it.
const String kVehicleModelAsset = 'assets/cars/toycar.glb';

/// Tesla-dark background so the model sits seamlessly in the dashboard.
const Color _kStage = Color(0xFF0A0A0B);

/// A rotatable, photoreal-ish 3D vehicle model rendered with Google's
/// `<model-viewer>` (PBR + image-based lighting) inside a WebView.
///
/// Quality knobs are deliberately tuned for a showroom look: a neutral studio
/// HDRI for clean reflections, soft contact shadows, a slightly low exposure
/// so highlights on car paint don't blow out, and a constrained orbit so the
/// camera can't dip below the floor or spin to an unflattering top-down angle.
class CarModel3D extends StatelessWidget {
  final String src;
  final String alt;

  const CarModel3D({
    super.key,
    this.src = kVehicleModelAsset,
    this.alt = 'Vehicle 3D model',
  });

  @override
  Widget build(BuildContext context) {
    return ColoredBox(
      color: _kStage,
      child: ModelViewer(
        // Re-create the viewer if the asset ever changes (per-vehicle swap).
        key: ValueKey(src),
        src: src,
        alt: alt,
        backgroundColor: _kStage,

        // ── Lighting & material quality ──────────────────────────────────
        // 'neutral' is model-viewer's built-in studio HDRI — even, flattering
        // light with real reflections on metallic paint, no external file.
        environmentImage: 'neutral',
        exposure: 0.85,
        shadowIntensity: 0.85,
        shadowSoftness: 0.9,

        // ── Camera & motion ──────────────────────────────────────────────
        cameraControls: true,
        disableZoom: false,
        autoRotate: true,
        autoRotateDelay: 1200,
        rotationPerSecond: '16deg',
        // Start at a 3/4 hero angle, pulled back a touch for breathing room.
        cameraOrbit: '30deg 75deg 110%',
        // Keep the camera between a low hero angle and just-above-eye-level,
        // so it never clips through the floor or flips to bird's-eye.
        minCameraOrbit: 'auto 55deg auto',
        maxCameraOrbit: 'auto 88deg auto',
        fieldOfView: '30deg',
        interpolationDecay: 220,

        // No AR in the prototype; no swipe-hand prompt over the auto-rotate.
        ar: false,
        interactionPrompt: InteractionPrompt.none,
        loading: Loading.eager,
      ),
    );
  }
}
