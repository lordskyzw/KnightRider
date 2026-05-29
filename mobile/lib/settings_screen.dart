import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'config.dart';

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
  Color _accent = AppPalette.accent;
  Color? _carColor; // null = factory paint

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final host = await PiConfig.host();
    final cloud = await PiConfig.cloudUrl();
    final car3d = await PiConfig.car3dEnabled();
    final carColorArgb = await PiConfig.carColor();
    setState(() {
      _hostCtrl.text = host;
      _cloudCtrl.text = cloud;
      _car3d = car3d;
      _carColor = carColorArgb == null ? null : Color(carColorArgb);
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
    await PiConfig.setAccentColor(_accent.toARGB32());
    await PiConfig.setCarColor(_carColor?.toARGB32());
    if (!mounted) return;
    Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: !_loaded
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
              children: [
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
                const SizedBox(height: 24),
                const Divider(color: AppPalette.divider),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('3D car model',
                      style: TextStyle(
                          color: AppPalette.textHi,
                          fontWeight: FontWeight.w600)),
                  subtitle: const Text(
                    'Show the rotatable 3D Vitz in the dashboard centre. '
                    'Turn off to use the flat silhouette (lighter).',
                    style: TextStyle(fontSize: 12, color: AppPalette.textMid),
                  ),
                  value: _car3d,
                  onChanged: (v) => setState(() => _car3d = v),
                ),
                const SizedBox(height: 16),
                const Divider(color: AppPalette.divider),
                const SizedBox(height: 12),
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
                const Divider(color: AppPalette.divider),
                const SizedBox(height: 12),
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
