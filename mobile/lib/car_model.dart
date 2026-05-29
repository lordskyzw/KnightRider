import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:model_viewer_plus/model_viewer_plus.dart';
import 'package:webview_flutter/webview_flutter.dart';

/// The glTF/GLB asset shown in the dashboard centre when the 3D feature flag
/// is on. Later this becomes a per-vehicle GLB delivered from the cloud; only
/// this constant (and the attribution) changes then.
const String kVehicleModelAsset = 'assets/cars/vitz.glb';

/// CC-BY attribution required by the model's licence. Must stay visible
/// wherever the model is shown. Source:
/// https://sketchfab.com/3d-models/toyota-vitz-0d1428782a5f4cf69efd8a15744e1d49
const String kVehicleModelCredit = 'Vitz by Driving501 · CC BY 4.0';

/// Which of the car's lamps are lit. Driven live from DBC signals (or the
/// manual Settings toggle for [head]).
class LampState {
  final bool head; // head/low-beam + fog + DRLs
  final bool brake; // taillights bright red
  final bool left; // left indicator
  final bool right; // right indicator
  final bool reverse;
  const LampState({
    this.head = false,
    this.brake = false,
    this.left = false,
    this.right = false,
    this.reverse = false,
  });

  @override
  bool operator ==(Object other) =>
      other is LampState &&
      other.head == head &&
      other.brake == brake &&
      other.left == left &&
      other.right == right &&
      other.reverse == reverse;

  @override
  int get hashCode => Object.hash(head, brake, left, right, reverse);
}

/// A rotatable 3D vehicle model (`<model-viewer>` in a transparent WebView).
///
/// The Vitz GLB has 37 named materials. We recolour/illuminate selectively by
/// material name, and — crucially — push changes via `runJavaScript` so the
/// WebView is NOT reloaded when colours or lamps change (a reload costs the
/// full WebGL warm-up, which would be fatal for live signal-driven lamps).
///   * [bodyColor]  → the `Paint` material (body only). null = factory silver.
///   * [wheelColor] → the `tire`/wheel material. Defaults to black.
///   * [lamps]      → per-lamp emissive state.
class CarModel3D extends StatefulWidget {
  final String src;
  final String alt;
  /// CC-BY attribution for [src]. Required to stay visible by the model's
  /// licence; defaults to the Vitz credit for the bundled default asset.
  final String credit;
  final Color? bodyColor;
  final Color wheelColor;
  final LampState lamps;

  const CarModel3D({
    super.key,
    this.src = kVehicleModelAsset,
    this.alt = 'Vehicle 3D model',
    this.credit = kVehicleModelCredit,
    this.bodyColor,
    this.wheelColor = const Color(0xFF000000),
    this.lamps = const LampState(),
  });

  @override
  State<CarModel3D> createState() => _CarModel3DState();
}

class _CarModel3DState extends State<CarModel3D> {
  WebViewController? _controller;
  bool _ready = false;

  static double _lin(double s) =>
      s <= 0.04045 ? s / 12.92 : math.pow((s + 0.055) / 1.055, 2.4).toDouble();

  /// JS assignments that set window.__krState to the current widget values.
  String _stateJs() {
    final b = widget.bodyColor;
    final haveBody = b != null;
    final br = haveBody ? _lin(b.r) : 0.0;
    final bg = haveBody ? _lin(b.g) : 0.0;
    final bb = haveBody ? _lin(b.b) : 0.0;
    final w = widget.wheelColor;
    final l = widget.lamps;
    return 'window.__krState={'
        'body:[$br,$bg,$bb],haveBody:$haveBody,'
        'wheel:[${_lin(w.r)},${_lin(w.g)},${_lin(w.b)}],'
        'head:${l.head},brake:${l.brake},left:${l.left},'
        'right:${l.right},reverse:${l.reverse}};';
  }

  /// One-time script: defines krApply + the load/ready hooks, then applies the
  /// initial state. Subsequent changes go through [_push] (no reload).
  String _initJs() {
    return '''
const mv = document.querySelector('model-viewer');
${_stateJs()}
window.krApply = function() {
  if (!mv || !mv.model) return;
  const s = window.__krState;
  for (const m of mv.model.materials) {
    const n = (m.name || '').toLowerCase();
    try {
      if (s.haveBody && n.includes('paint')) {
        m.pbrMetallicRoughness.setBaseColorFactor([s.body[0], s.body[1], s.body[2], 1]);
      }
      if (n.includes('tire')) {
        m.pbrMetallicRoughness.setBaseColorFactor([s.wheel[0], s.wheel[1], s.wheel[2], 1]);
      }
      if (n.includes('lowbeam') || n.includes('foglight') || n.includes('vehiclelights')) {
        m.setEmissiveFactor(s.head ? [1.0, 0.92, 0.75] : [0, 0, 0]);
      } else if (n.includes('tail')) {
        m.setEmissiveFactor(s.brake ? [1.0, 0, 0] : (s.head ? [0.35, 0, 0] : [0, 0, 0]));
      } else if (n.includes('reverse')) {
        m.setEmissiveFactor(s.reverse ? [0.85, 0.85, 0.85] : [0, 0, 0]);
      } else if (n.includes('signal_l')) {
        m.setEmissiveFactor(s.left ? [1.0, 0.5, 0] : [0, 0, 0]);
      } else if (n.includes('signal_r')) {
        m.setEmissiveFactor(s.right ? [1.0, 0.5, 0] : [0, 0, 0]);
      }
    } catch (e) {}
  }
};
mv.addEventListener('load', function() {
  window.krApply();
  try { KRReady.postMessage('1'); } catch (e) {}
});
window.krApply();
''';
  }

  /// Push the current widget state into the already-loaded viewer.
  void _push() {
    final c = _controller;
    if (c == null || !_ready) return;
    c.runJavaScript('${_stateJs()}window.krApply&&window.krApply();');
  }

  @override
  void didUpdateWidget(CarModel3D old) {
    super.didUpdateWidget(old);
    if (old.bodyColor != widget.bodyColor ||
        old.wheelColor != widget.wheelColor ||
        old.lamps != widget.lamps) {
      _push();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        // Behind the (transparent) viewer during WebGL warm-up; the car covers
        // it once the model paints.
        const Center(
          child: SizedBox(
            width: 26,
            height: 26,
            child: CircularProgressIndicator(
                strokeWidth: 2, color: Color(0xFF5A5A5E)),
          ),
        ),
        Positioned.fill(child: _viewer()),
        Positioned(
          left: 0,
          right: 0,
          bottom: 4,
          child: Center(
            child: Text(
              widget.credit,
              style: const TextStyle(
                  fontSize: 9, color: Color(0xFF5A5A5E), letterSpacing: 0.2),
            ),
          ),
        ),
      ],
    );
  }

  Widget _viewer() {
    return ModelViewer(
      // Key only on src: appearance changes are pushed via JS, never a reload.
      key: ValueKey(widget.src),
      src: widget.src,
      alt: widget.alt,
      backgroundColor: Colors.transparent,
      relatedJs: _initJs(),
      javascriptChannels: {
        JavascriptChannel('KRReady', onMessageReceived: (_) {
          _ready = true;
          _push();
        }),
      },
      onWebViewCreated: (c) => _controller = c,

      // Lighting & quality
      environmentImage: 'neutral',
      exposure: 1.05,
      shadowIntensity: 0.7,
      shadowSoftness: 1.0,

      // Camera & motion — no auto-rotate, still drag-to-orbit.
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
