import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'config.dart';
import 'vehicle_catalog.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _hostCtrl = TextEditingController();
  final _cloudCtrl = TextEditingController();
  bool _loaded = false;
  bool _car3d = true;
  bool _autoRotate = true;
  String _bgId = 'carbon';
  String _vehicleId = kDefaultVehicleId;
  Color _accent = AppPalette.accent;
  Color? _carColor; // null = factory paint
  Color _wheelColor = const Color(0xFF0A0A0A);
  bool _lightsOn = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final host = await PiConfig.host();
    final cloud = await PiConfig.cloudUrl();
    final car3d = await PiConfig.car3dEnabled();
    final autoRotate = await PiConfig.carAutoRotate();
    final bgId = await PiConfig.carBackground();
    final vehicleId = await PiConfig.vehicleId();
    final carColorArgb = await PiConfig.carColor();
    final wheelArgb = await PiConfig.wheelColor();
    final lightsOn = await PiConfig.lightsOn();
    setState(() {
      _hostCtrl.text = host;
      _cloudCtrl.text = cloud;
      _car3d = car3d;
      _autoRotate = autoRotate;
      _bgId = bgId;
      _vehicleId = vehicleId;
      _carColor = carColorArgb == null ? null : Color(carColorArgb);
      _wheelColor = Color(wheelArgb);
      _lightsOn = lightsOn;
      _accent = AppPalette.accent;
      _loaded = true;
    });
  }

  @override
  void dispose() {
    _hostCtrl.dispose();
    _cloudCtrl.dispose();
    super.dispose();
  }

  /// Apply the accent immediately as a live preview (persisted only on Save).
  void _previewAccent(Color c) {
    setState(() => _accent = c);
    AppPalette.accent = c;
    accentRevision.value++; // re-themes the whole app live
  }

  Future<void> _save() async {
    final host = _hostCtrl.text.trim();
    final cloud = _cloudCtrl.text.trim();
    if (host.isEmpty || cloud.isEmpty) return;
    await PiConfig.setHost(host);
    await PiConfig.setCloudUrl(cloud);
    await PiConfig.setCar3dEnabled(_car3d);
    await PiConfig.setCarAutoRotate(_autoRotate);
    await PiConfig.setCarBackground(_bgId);
    await PiConfig.setVehicleId(_vehicleId);
    await PiConfig.setAccentColor(_accent.toARGB32());
    await PiConfig.setCarColor(_carColor?.toARGB32());
    await PiConfig.setWheelColor(_wheelColor.toARGB32());
    await PiConfig.setLightsOn(_lightsOn);
    if (!mounted) return;
    Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
        actions: [
          if (_loaded)
            TextButton(
              onPressed: _save,
              child: const Text('Save',
                  style: TextStyle(fontWeight: FontWeight.w700)),
            ),
        ],
      ),
      body: !_loaded
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
              children: [
                // ── Connection ──────────────────────────────────────────────
                const _SectionHeader('Connection', first: true),
                const _Label('PI HOST:PORT'),
                const SizedBox(height: 8),
                TextField(
                  controller: _hostCtrl,
                  style: const TextStyle(color: AppPalette.textHi),
                  decoration:
                      const InputDecoration(hintText: 'kitt.local:8080'),
                ),
                const SizedBox(height: 24),
                const _Label('CLOUD BASE URL'),
                const SizedBox(height: 4),
                const Text(
                  'Where the courier uploads batches when off-LAN.',
                  style: TextStyle(fontSize: 12, color: AppPalette.textMid),
                ),
                const SizedBox(height: 8),
                TextField(
                  controller: _cloudCtrl,
                  style: const TextStyle(color: AppPalette.textHi),
                  decoration: const InputDecoration(
                    hintText:
                        'https://knight-rider-cloud-production.up.railway.app',
                  ),
                ),

                // ── Vehicle ─────────────────────────────────────────────────
                const _SectionHeader('Vehicle'),
                const Text(
                  "Which car to render. VIN can't be read over OBD reliably, "
                  'so choose it here.',
                  style: TextStyle(fontSize: 12, color: AppPalette.textMid),
                ),
                const SizedBox(height: 8),
                for (final v in kVehicles)
                  _VehicleRow(
                    vehicle: v,
                    selected: v.id == _vehicleId,
                    onTap: () => setState(() => _vehicleId = v.id),
                  ),

                // ── Appearance ──────────────────────────────────────────────
                const _SectionHeader('Appearance'),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('3D car model',
                      style: TextStyle(
                          color: AppPalette.textHi,
                          fontWeight: FontWeight.w600)),
                  subtitle: const Text(
                    'Show the rotatable 3D car in the dashboard centre. '
                    'Turn off to use the flat silhouette (lighter).',
                    style: TextStyle(fontSize: 12, color: AppPalette.textMid),
                  ),
                  value: _car3d,
                  onChanged: (v) => setState(() => _car3d = v),
                ),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Auto-rotate',
                      style: TextStyle(
                          color: AppPalette.textHi,
                          fontWeight: FontWeight.w600)),
                  subtitle: const Text(
                    'Slowly turn the car like a showroom turntable. '
                    'Dragging always still orbits it.',
                    style: TextStyle(fontSize: 12, color: AppPalette.textMid),
                  ),
                  value: _autoRotate,
                  onChanged: _car3d ? (v) => setState(() => _autoRotate = v) : null,
                ),
                const SizedBox(height: 16),
                const _Label('BACKGROUND'),
                const SizedBox(height: 4),
                const Text(
                  'The backdrop behind the car. All kept dark so the car stays '
                  'the focus.',
                  style: TextStyle(fontSize: 12, color: AppPalette.textMid),
                ),
                const SizedBox(height: 14),
                _BackgroundRow(
                  selectedId: _bgId,
                  onPick: (id) => setState(() => _bgId = id),
                ),
                const SizedBox(height: 24),
                const _Label('CAR COLOUR'),
                const SizedBox(height: 4),
                const Text(
                  'Repaints the 3D car. Factory keeps the original silver.',
                  style: TextStyle(fontSize: 12, color: AppPalette.textMid),
                ),
                const SizedBox(height: 14),
                _CarColorRow(
                  selected: _carColor,
                  onPick: (c) => setState(() => _carColor = c),
                ),
                const SizedBox(height: 24),
                const _Label('WHEEL COLOUR'),
                const SizedBox(height: 14),
                _SwatchRow(
                  options: kWheelColorOptions,
                  selected: _wheelColor,
                  onPick: (c) => setState(() => _wheelColor = c),
                ),
                const SizedBox(height: 8),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Lights',
                      style: TextStyle(
                          color: AppPalette.textHi,
                          fontWeight: FontWeight.w600)),
                  subtitle: const Text(
                    'Illuminate the car\'s head, tail, fog and indicator lamps.',
                    style: TextStyle(fontSize: 12, color: AppPalette.textMid),
                  ),
                  value: _lightsOn,
                  onChanged: (v) => setState(() => _lightsOn = v),
                ),

                // ── Theme ───────────────────────────────────────────────────
                const _SectionHeader('Theme'),
                const _Label('ACCENT COLOUR'),
                const SizedBox(height: 4),
                const Text(
                  'Tints the whole app — gauges, glow, highlights.',
                  style: TextStyle(fontSize: 12, color: AppPalette.textMid),
                ),
                const SizedBox(height: 14),
                _AccentRow(selected: _accent, onPick: _previewAccent),
                const SizedBox(height: 28),
                Align(
                  alignment: Alignment.centerRight,
                  child: FilledButton(
                    onPressed: _save,
                    child: const Text('Save'),
                  ),
                ),
              ],
            ),
    );
  }
}

/// A prominent section divider grouping related settings (Connection, Vehicle,
/// Appearance, Theme). Accent-tinted so the eye can scan the page by section.
class _SectionHeader extends StatelessWidget {
  final String text;
  final bool first;
  const _SectionHeader(this.text, {this.first = false});
  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(top: first ? 4 : 28, bottom: 12),
      child: Row(
        children: [
          Text(text.toUpperCase(),
              style: TextStyle(
                fontSize: 12,
                color: AppPalette.accent,
                fontWeight: FontWeight.w800,
                letterSpacing: 2.5,
              )),
          const SizedBox(width: 12),
          const Expanded(child: Divider(color: AppPalette.divider)),
        ],
      ),
    );
  }
}

/// Swatch row for the car backdrops — each chip previews the actual gradient.
class _BackgroundRow extends StatelessWidget {
  final String selectedId;
  final ValueChanged<String> onPick;
  const _BackgroundRow({required this.selectedId, required this.onPick});

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 14,
      runSpacing: 14,
      children: [
        for (final bg in kCarBackgrounds)
          GestureDetector(
            onTap: () => onPick(bg.id),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    gradient: bg.gradient,
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: bg.id == selectedId
                          ? AppPalette.textHi
                          : AppPalette.divider,
                      width: 3,
                    ),
                  ),
                ),
                const SizedBox(height: 6),
                SizedBox(
                  width: 56,
                  child: Text(bg.name,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                          fontSize: 9, color: AppPalette.textMid)),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class _Label extends StatelessWidget {
  final String text;
  const _Label(this.text);
  @override
  Widget build(BuildContext context) {
    return Text(text,
        style: const TextStyle(
          fontSize: 11,
          color: AppPalette.textMid,
          fontWeight: FontWeight.w700,
          letterSpacing: 2,
        ));
  }
}

/// A tappable vehicle choice — custom (not RadioListTile, whose group API is
/// deprecated) to match the app's hand-rolled pickers.
class _VehicleRow extends StatelessWidget {
  final VehicleModel vehicle;
  final bool selected;
  final VoidCallback onTap;
  const _VehicleRow({
    required this.vehicle,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            Icon(
              selected
                  ? Icons.radio_button_checked
                  : Icons.radio_button_unchecked,
              size: 20,
              color: selected ? AppPalette.accent : AppPalette.textMid,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(vehicle.name,
                      style: const TextStyle(
                          color: AppPalette.textHi,
                          fontWeight: FontWeight.w600)),
                  const SizedBox(height: 2),
                  Text(
                    vehicle.has3d
                        ? '3D model available'
                        : '3D model coming soon — silhouette for now',
                    style: const TextStyle(
                        fontSize: 11, color: AppPalette.textMid),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// A row of solid-colour swatches (non-null), e.g. wheel colours.
class _SwatchRow extends StatelessWidget {
  final List<CarColorOption> options;
  final Color selected;
  final ValueChanged<Color> onPick;
  const _SwatchRow({
    required this.options,
    required this.selected,
    required this.onPick,
  });

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 14,
      runSpacing: 14,
      children: [
        for (final opt in options)
          GestureDetector(
            onTap: () => onPick(opt.tint!),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: opt.tint,
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: selected.toARGB32() == opt.tint!.toARGB32()
                          ? AppPalette.textHi
                          : AppPalette.divider,
                      width: 3,
                    ),
                  ),
                ),
                const SizedBox(height: 6),
                SizedBox(
                  width: 56,
                  child: Text(opt.name,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                          fontSize: 9, color: AppPalette.textMid)),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class _CarColorRow extends StatelessWidget {
  final Color? selected;
  final ValueChanged<Color?> onPick;
  const _CarColorRow({required this.selected, required this.onPick});

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 14,
      runSpacing: 14,
      children: [
        for (final opt in kCarColorOptions)
          () {
            final isFactory = opt.tint == null;
            // Factory shows as silver (matches the stock model).
            final swatch = opt.tint ?? const Color(0xFFC4C6CA);
            final isSel = selected?.toARGB32() == opt.tint?.toARGB32();
            return GestureDetector(
              onTap: () => onPick(opt.tint),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    width: 44,
                    height: 44,
                    decoration: BoxDecoration(
                      color: swatch,
                      shape: BoxShape.circle,
                      border: Border.all(
                        color: isSel ? AppPalette.textHi : Colors.transparent,
                        width: 3,
                      ),
                    ),
                    child: isFactory
                        ? const Icon(Icons.directions_car,
                            size: 20, color: Color(0xFF3A3B3F))
                        : null,
                  ),
                  const SizedBox(height: 6),
                  SizedBox(
                    width: 56,
                    child: Text(opt.name,
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                            fontSize: 9, color: AppPalette.textMid)),
                  ),
                ],
              ),
            );
          }(),
      ],
    );
  }
}

class _AccentRow extends StatelessWidget {
  final Color selected;
  final ValueChanged<Color> onPick;
  const _AccentRow({required this.selected, required this.onPick});

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 14,
      runSpacing: 14,
      children: [
        for (final opt in kAccentOptions)
          GestureDetector(
            onTap: () => onPick(opt.color),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: opt.color,
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: selected.toARGB32() == opt.color.toARGB32()
                          ? AppPalette.textHi
                          : Colors.transparent,
                      width: 3,
                    ),
                    boxShadow: [
                      BoxShadow(
                        color: opt.color.withValues(alpha: 0.45),
                        blurRadius: 12,
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 6),
                SizedBox(
                  width: 56,
                  child: Text(opt.name,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                          fontSize: 9, color: AppPalette.textMid)),
                ),
              ],
            ),
          ),
      ],
    );
  }
}
