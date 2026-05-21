import 'package:flutter/material.dart';

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

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final host = await PiConfig.host();
    final cloud = await PiConfig.cloudUrl();
    setState(() {
      _hostCtrl.text = host;
      _cloudCtrl.text = cloud;
      _loaded = true;
    });
  }

  @override
  void dispose() {
    _hostCtrl.dispose();
    _cloudCtrl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final host = _hostCtrl.text.trim();
    final cloud = _cloudCtrl.text.trim();
    if (host.isEmpty || cloud.isEmpty) return;
    await PiConfig.setHost(host);
    await PiConfig.setCloudUrl(cloud);
    if (!mounted) return;
    Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: !_loaded
            ? const Center(child: CircularProgressIndicator())
            : Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Pi host:port',
                      style: TextStyle(fontWeight: FontWeight.bold)),
                  const SizedBox(height: 8),
                  TextField(
                    controller: _hostCtrl,
                    decoration: const InputDecoration(
                      border: OutlineInputBorder(),
                      hintText: 'raspberrypi.local:8080',
                    ),
                  ),
                  const SizedBox(height: 24),
                  const Text('Cloud base URL',
                      style: TextStyle(fontWeight: FontWeight.bold)),
                  const SizedBox(height: 4),
                  const Text(
                    'Where the courier uploads batches when off-LAN.',
                    style: TextStyle(fontSize: 12, color: Colors.grey),
                  ),
                  const SizedBox(height: 8),
                  TextField(
                    controller: _cloudCtrl,
                    decoration: const InputDecoration(
                      border: OutlineInputBorder(),
                      hintText: 'https://knight-rider-cloud-production.up.railway.app',
                    ),
                  ),
                  const SizedBox(height: 16),
                  Align(
                    alignment: Alignment.centerRight,
                    child: FilledButton(
                      onPressed: _save,
                      child: const Text('Save'),
                    ),
                  ),
                ],
              ),
      ),
    );
  }
}
