import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:model_viewer_plus/model_viewer_plus.dart';
import 'package:webview_flutter/webview_flutter.dart';

import 'sensor_map.dart';
import 'vehicle_catalog.dart';

/// The glTF/GLB asset shown in the dashboard centre when the 3D feature flag
/// is on. Later this becomes a per-vehicle GLB delivered from the cloud; only
/// this constant (and the attribution) changes then.
const String kVehicleModelAsset = 'assets/cars/vitz.glb';

/// Test seam: `flutter test` has no `WebViewPlatform`, so a real `ModelViewer`
/// asserts on build. Set this true in widget tests to render a stub instead.
/// Always false in the app — the device experience is the real 3D model.
bool debugDisableCarWebView = false;

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
  /// Material-name map for this GLB (which materials are body/wheel/lamps).
  final MaterialMap materials;
  /// Gentle showroom turntable rotation. Drag-to-orbit still works either way.
  final bool autoRotate;
  /// X-ray / in-depth mode: ghost the body and reveal glowing sensor hotspots.
  final bool inDepth;
  /// Sensor nodes to anchor as hotspots (positions normalised to the bbox).
  final List<SensorNode> sensorNodes;
  /// Per-node status (`live` / `available` / `fault`) → hotspot colour.
  final Map<String, String> sensorStatus;
  /// Sign of the model's "front" along Z (+1 or -1) — orientation differs per
  /// GLB. Tuned per vehicle so anatomical hotspots land on the right end.
  final double frontZ;
  /// Tapped a sensor hotspot (passes the node id).
  final void Function(String id)? onHotspotTap;
  final Color? bodyColor;
  final Color wheelColor;
  final LampState lamps;

  const CarModel3D({
    super.key,
    this.src = kVehicleModelAsset,
    this.alt = 'Vehicle 3D model',
    this.credit = kVehicleModelCredit,
    this.materials = const MaterialMap(),
    this.autoRotate = true,
    this.inDepth = false,
    this.sensorNodes = kSensorNodes,
    this.sensorStatus = const {},
    this.frontZ = 1.0,
    this.onHotspotTap,
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

  /// JS assignments for the current widget values: colours, lamps, material
  /// map, in-depth flag, per-node sensor status, and the front-Z orientation.
  String _stateJs() {
    final b = widget.bodyColor;
    final haveBody = b != null;
    final br = haveBody ? _lin(b.r) : 0.0;
    final bg = haveBody ? _lin(b.g) : 0.0;
    final bb = haveBody ? _lin(b.b) : 0.0;
    final w = widget.wheelColor;
    final l = widget.lamps;
    final m = widget.materials;
    final map = 'map:{'
        'body:${jsonEncode(m.body)},wheel:${jsonEncode(m.wheel)},'
        'head:${jsonEncode(m.head)},tail:${jsonEncode(m.tail)},'
        'signalL:${jsonEncode(m.signalL)},signalR:${jsonEncode(m.signalR)},'
        'reverse:${jsonEncode(m.reverse)}}';
    return 'window.__krState={'
        'body:[$br,$bg,$bb],haveBody:$haveBody,'
        'wheel:[${_lin(w.r)},${_lin(w.g)},${_lin(w.b)}],'
        'head:${l.head},brake:${l.brake},left:${l.left},'
        'right:${l.right},reverse:${l.reverse},inDepth:${widget.inDepth},$map};'
        'window.__krSensors=${jsonEncode(widget.sensorStatus)};'
        'window.__krFrontZ=${widget.frontZ};';
  }

  /// Glowing sensor hotspots, slotted into the `<model-viewer>`. Positions are
  /// set in JS from the model's bounding box on load (see krPlace).
  String _hotspotHtml() => widget.sensorNodes
      .map((n) => '<button class="kr-hot" slot="hotspot-${n.id}" '
          'data-id="${n.id}" data-nx="${n.nx}" data-ny="${n.ny}" '
          'data-nz="${n.nz}" data-position="0m 0m 0m" data-normal="0 1 0"></button>')
      .join();

  static const String _relatedCss = '''
.kr-hot{width:16px;height:16px;border-radius:50%;border:2px solid rgba(255,255,255,.85);
  background:rgba(255,255,255,.18);cursor:pointer;padding:0;display:none;
  transition:transform .15s ease;}
.kr-hot:hover{transform:scale(1.3);}
.kr-hot.live{border-color:#22c55e;background:#22c55e;
  box-shadow:0 0 12px 3px rgba(34,197,94,.8);animation:krpulse 1.6s infinite;}
.kr-hot.available{border-color:#9aa0aa;background:rgba(154,160,170,.4);}
.kr-hot.fault{border-color:#ef4444;background:#ef4444;
  box-shadow:0 0 14px 4px rgba(239,68,68,.85);animation:krpulse .9s infinite;}
@keyframes krpulse{0%,100%{opacity:1}50%{opacity:.4}}
''';

  /// One-time script: defines krApply (recolour + lamps + ghost), hotspot
  /// placement, recenter, and the load/ready hooks. Updates go via [_push].
  String _initJs() {
    return '''
const mv = document.querySelector('model-viewer');
${_stateJs()}
window.krMatch = function(name, subs) {
  if (!subs || !subs.length) return false;
  const n = (name || '').toLowerCase();
  return subs.some(function(s){ return n.includes(String(s).toLowerCase()); });
};
let krOrig = null;
window.krSnapshot = function() {
  krOrig = {};
  if (!mv.model) return;
  for (const m of mv.model.materials) {
    try { krOrig[m.name] = {
      bcf: m.pbrMetallicRoughness.baseColorFactor.slice(), am: m.getAlphaMode() };
    } catch (e) {}
  }
};
window.krPlace = function() {
  try {
    const c = mv.getBoundingBoxCenter(); const d = mv.getDimensions();
    const fz = window.__krFrontZ || 1;
    document.querySelectorAll('.kr-hot').forEach(function(el){
      const nx=+el.dataset.nx, ny=+el.dataset.ny, nz=+el.dataset.nz;
      const x=c.x+nx*d.x/2, y=c.y+ny*d.y/2, z=c.z+nz*fz*d.z/2;
      mv.updateHotspot({ name: el.getAttribute('slot'), position: x+'m '+y+'m '+z+'m' });
    });
  } catch (e) {}
};
window.krApply = function() {
  if (!mv.model) return;
  const s = window.__krState; const M = s.map || {}; const dep = s.inDepth;
  for (const m of mv.model.materials) {
    const n = m.name || ''; const o = krOrig ? krOrig[n] : null;
    let rgb = null;
    if (s.haveBody && window.krMatch(n, M.body)) rgb = s.body;
    else if (window.krMatch(n, M.wheel)) rgb = s.wheel;
    try {
      if (dep) {
        // Ghost everything translucent; sensor hotspots float "inside".
        const a = 0.18; const base = rgb ? rgb : (o ? o.bcf : [1,1,1]);
        m.setAlphaMode('BLEND');
        m.pbrMetallicRoughness.setBaseColorFactor([base[0], base[1], base[2], a]);
        m.setEmissiveFactor([0,0,0]);
      } else {
        if (o) { m.setAlphaMode(o.am); m.pbrMetallicRoughness.setBaseColorFactor(o.bcf); }
        if (rgb) m.pbrMetallicRoughness.setBaseColorFactor([rgb[0], rgb[1], rgb[2], o?o.bcf[3]:1]);
        if (window.krMatch(n, M.head)) {
          m.setEmissiveFactor(s.head ? [1.0,0.92,0.75] : [0,0,0]);
        } else if (window.krMatch(n, M.tail)) {
          m.setEmissiveFactor(s.brake ? [1.0,0,0] : (s.head ? [0.35,0,0] : [0,0,0]));
        } else if (window.krMatch(n, M.reverse)) {
          m.setEmissiveFactor(s.reverse ? [0.85,0.85,0.85] : [0,0,0]);
        } else if (window.krMatch(n, M.signalL)) {
          m.setEmissiveFactor(s.left ? [1.0,0.5,0] : [0,0,0]);
        } else if (window.krMatch(n, M.signalR)) {
          m.setEmissiveFactor(s.right ? [1.0,0.5,0] : [0,0,0]);
        }
      }
    } catch (e) {}
  }
  const sens = window.__krSensors || {};
  document.querySelectorAll('.kr-hot').forEach(function(el){
    el.style.display = dep ? 'block' : 'none';
    const st = sens[el.dataset.id] || 'available';
    el.classList.remove('live','available','fault'); el.classList.add(st);
  });
};
window.krRecenter = function() {
  try {
    mv.cameraTarget = 'auto auto auto';
    mv.cameraOrbit = '28deg 72deg 115%';
    mv.fieldOfView = '30deg';
    if (mv.jumpCameraToGoal) mv.jumpCameraToGoal();
  } catch (e) {}
};
mv.addEventListener('load', function() {
  window.krSnapshot(); window.krPlace(); window.krApply();
  try { KRReady.postMessage('1'); } catch (e) {}
});
document.querySelectorAll('.kr-hot').forEach(function(el){
  el.addEventListener('click', function(){
    try { KRHotspot.postMessage(el.dataset.id); } catch (e) {}
  });
});
window.krApply();
''';
  }

  /// Push the current widget state into the already-loaded viewer (no reload).
  void _push() {
    final c = _controller;
    if (c == null || !_ready) return;
    c.runJavaScript('${_stateJs()}window.krApply&&window.krApply();');
  }

  void _recenter() {
    _controller?.runJavaScript('window.krRecenter&&window.krRecenter();');
  }

  @override
  void didUpdateWidget(CarModel3D old) {
    super.didUpdateWidget(old);
    if (old.bodyColor != widget.bodyColor ||
        old.wheelColor != widget.wheelColor ||
        old.lamps != widget.lamps ||
        old.inDepth != widget.inDepth ||
        !mapEquals(old.sensorStatus, widget.sensorStatus)) {
      _push();
    }
  }

  @override
  Widget build(BuildContext context) {
    if (debugDisableCarWebView) return const SizedBox.expand();
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
        // Recenter: reset zoom/pan/orbit if the user gets lost up close.
        Positioned(
          right: 2,
          bottom: 2,
          child: IconButton(
            iconSize: 18,
            visualDensity: VisualDensity.compact,
            onPressed: _recenter,
            icon: const Icon(Icons.center_focus_strong_outlined,
                color: Color(0xFF7A7A7E)),
            tooltip: 'Recenter',
          ),
        ),
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
      relatedCss: _relatedCss,
      innerModelViewerHtml: _hotspotHtml(),
      javascriptChannels: {
        JavascriptChannel('KRReady', onMessageReceived: (_) {
          _ready = true;
          _push();
        }),
        JavascriptChannel('KRHotspot', onMessageReceived: (msg) {
          widget.onHotspotTap?.call(msg.message);
        }),
      },
      onWebViewCreated: (c) => _controller = c,

      // Lighting & quality
      environmentImage: 'neutral',
      exposure: 1.05,
      shadowIntensity: 0.7,
      shadowSoftness: 1.0,

      // Camera & motion — gentle showroom turntable (toggleable); drag still orbits.
      cameraControls: true,
      disableZoom: false,
      autoRotate: widget.autoRotate,
      autoRotateDelay: 0,
      rotationPerSecond: '14deg',
      // Pulled back (115%) so the car keeps a comfortable margin at every
      // rotation angle, not just top-down. Recenter returns here.
      cameraOrbit: '28deg 72deg 115%',
      minCameraOrbit: 'auto 40deg 85%',
      maxCameraOrbit: 'auto 90deg 220%',
      fieldOfView: '30deg',
      interpolationDecay: 220,

      ar: false,
      interactionPrompt: InteractionPrompt.none,
      loading: Loading.eager,
    );
  }
}
