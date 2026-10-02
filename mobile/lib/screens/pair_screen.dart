import 'dart:convert';

import 'package:flutter/material.dart';

import '../services/edge_client.dart';
import 'relay_pair_screen.dart';
import 'stream_screen.dart';

/// Pair this phone with the edge workstation on the factory LAN.
///
/// The desktop shows a QR code containing {host, code, name}; scanning is a
/// planned addition — for now the same values are typed once here.
class PairScreen extends StatefulWidget {
  const PairScreen({super.key, required this.orgId, required this.orgName});

  final String orgId;
  final String orgName;

  @override
  State<PairScreen> createState() => _PairScreenState();
}

class _PairScreenState extends State<PairScreen> {
  final _host = TextEditingController(text: 'http://192.168.1.10:8765');
  final _code = TextEditingController();
  final _deviceName = TextEditingController(text: 'Operator phone');
  bool _busy = false;
  String? _error;
  String? _status;

  Future<void> _checkHealth() async {
    final client = EdgeClient(_host.text);
    setState(() => _status = 'Checking edge…');
    try {
      final health = await client.health();
      setState(() {
        _status =
            'Edge reachable · model ${health['model_version'] ?? 'none'} · '
            'paired devices ${health['paired_devices']}';
      });
    } catch (error) {
      setState(() => _status = 'Edge not reachable: $error');
    }
  }

  Future<void> _pair() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final client = EdgeClient(_host.text);
    try {
      await client.pair(
        code: _code.text.trim(),
        name: _deviceName.text.trim().isEmpty
            ? 'Operator phone'
            : _deviceName.text.trim(),
      );
      if (!mounted) return;
      Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => StreamScreen(
            edge: client,
            orgId: widget.orgId,
            orgName: widget.orgName,
          ),
        ),
      );
    } catch (error) {
      setState(() => _error = '$error');
    } finally {
      setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('Pair with edge — ${widget.orgName}')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text(
                'Enter the edge address and pairing code shown on the '
                'VisionQC desktop app (Cameras page).',
                style: TextStyle(color: Colors.black54),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _host,
                decoration: const InputDecoration(
                  labelText: 'Edge address',
                  hintText: 'http://192.168.1.10:8765',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _code,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'Pairing code',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _deviceName,
                decoration: const InputDecoration(
                  labelText: 'This device name',
                  border: OutlineInputBorder(),
                ),
              ),
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(_error!, style: const TextStyle(color: Colors.red)),
              ],
              if (_status != null) ...[
                const SizedBox(height: 12),
                Text(_status!, style: const TextStyle(color: Colors.black54)),
              ],
              const SizedBox(height: 16),
              OutlinedButton(
                onPressed: _busy ? null : _checkHealth,
                child: const Text('Check edge connection'),
              ),
              const SizedBox(height: 8),
              FilledButton(
                onPressed: _busy ? null : _pair,
                child: _busy
                    ? const SizedBox(
                        height: 18,
                        width: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Pair and open camera'),
              ),
              const SizedBox(height: 20),
              const Divider(),
              const SizedBox(height: 8),
              const Text(
                'Off-site? Stream this phone through the cloud relay instead '
                '(server operator provides the camera ID and API key).',
                style: TextStyle(fontSize: 12, color: Colors.black54),
              ),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: () {
                  Navigator.of(context).push(
                    MaterialPageRoute(
                      builder: (_) => const RelayPairScreen(),
                    ),
                  );
                },
                icon: const Icon(Icons.cloud_outlined),
                label: const Text('Use the cloud relay'),
              ),
              const SizedBox(height: 24),
              FutureBuilder<String>(
                future: _qrHint(),
                builder: (context, snapshot) => Text(
                  snapshot.data ?? '',
                  style: const TextStyle(fontSize: 11, color: Colors.black38),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<String> _qrHint() async {
    // Reserved for the QR-scan upgrade; keeps the payload format documented.
    return 'QR payload format: '
        '${jsonEncode({'host': 'http://<edge>:8765', 'code': '<6 digits>', 'name': '<edge>'})}';
  }
}
