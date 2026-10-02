import 'dart:convert';

import 'package:flutter/material.dart';

import '../services/relay_client.dart';
import 'qr_scan_screen.dart';
import 'relay_stream_screen.dart';

/// Connect this phone to the VisionQC relay as a camera.
///
/// The fast path is a single QR scan: the desktop shows a code, the phone
/// reads it, exchanges the one-time token for credentials and starts
/// streaming. Manual entry stays available under "Enter details manually".
class RelayPairScreen extends StatefulWidget {
  const RelayPairScreen({super.key, this.defaultServer});

  /// Pre-filled relay address, e.g. `wss://visionqc.example.com`.
  final String? defaultServer;

  @override
  State<RelayPairScreen> createState() => _RelayPairScreenState();
}

class _RelayPairScreenState extends State<RelayPairScreen> {
  late final TextEditingController _server = TextEditingController(
    text: widget.defaultServer ?? 'wss://visionqc.tavesglobal.com',
  );
  final _cameraId = TextEditingController();
  final _apiKey = TextEditingController();
  bool _busy = false;
  String? _error;
  String? _status;

  // ----------------------------------------------------------------- pairing

  Future<void> _scan() async {
    final raw = await Navigator.of(context).push<String>(
      MaterialPageRoute(builder: (_) => const QrScanScreen()),
    );
    if (raw == null || !mounted) return;
    await _pairWithCode(raw);
  }

  Future<void> _pairWithCode(String raw) async {
    final Map<String, dynamic> payload;
    try {
      payload = jsonDecode(raw) as Map<String, dynamic>;
    } catch (_) {
      setState(() => _error = 'That QR code is not a VisionQC pairing code.');
      return;
    }
    final server = payload['server'] as String?;
    final token = payload['token'] as String?;
    if (server == null || token == null) {
      setState(() => _error = 'That QR code is missing pairing details.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _status = 'Pairing…';
    });
    try {
      final credentials = await RelayClient.claimPairing(server, token);
      if (!mounted) return;
      setState(() => _status = 'Connecting…');
      final client = RelayClient(
        serverUrl: server,
        cameraId: credentials['camera_id'] as String,
        apiKey: credentials['api_key'] as String,
      );
      await _openStream(client);
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
          _status = null;
        });
      }
    }
  }

  Future<void> _connectManually() async {
    if (_cameraId.text.trim().isEmpty || _apiKey.text.trim().isEmpty) {
      setState(() => _error = 'Camera ID and API key are both needed.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _status = 'Connecting…';
    });
    final client = RelayClient(
      serverUrl: _server.text,
      cameraId: _cameraId.text.trim(),
      apiKey: _apiKey.text.trim(),
    );
    try {
      await _openStream(client);
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
          _status = null;
        });
      }
    }
  }

  Future<void> _openStream(RelayClient client) async {
    try {
      await client.connect();
    } catch (_) {
      client.dispose();
      rethrow;
    }
    if (!mounted) {
      client.dispose();
      return;
    }
    await Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => RelayStreamScreen(client: client)),
    );
  }

  @override
  void dispose() {
    _server.dispose();
    _cameraId.dispose();
    _apiKey.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Connect camera')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text(
                'Connect this phone to the VisionQC server',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
              ),
              const SizedBox(height: 8),
              const Text(
                'On the desktop, open Cameras → “Connect phone”. Then scan '
                'the QR code here — the phone connects and starts streaming '
                'by itself.',
                style: TextStyle(color: Colors.black54),
              ),
              const SizedBox(height: 24),
              FilledButton.icon(
                onPressed: _busy ? null : _scan,
                style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 18),
                ),
                icon: _busy
                    ? const SizedBox(
                        height: 18,
                        width: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.qr_code_scanner),
                label: const Text(
                  'Scan QR code',
                  style: TextStyle(fontSize: 16),
                ),
              ),
              if (_status != null) ...[
                const SizedBox(height: 12),
                Text(_status!, style: const TextStyle(color: Colors.black54)),
              ],
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(_error!, style: const TextStyle(color: Colors.red)),
              ],
              const SizedBox(height: 12),
              Theme(
                data: Theme.of(context)
                    .copyWith(dividerColor: Colors.transparent),
                child: ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  childrenPadding: const EdgeInsets.only(bottom: 8),
                  title: const Text(
                    'Enter details manually',
                    style: TextStyle(fontSize: 14),
                  ),
                  children: [
                    TextField(
                      controller: _server,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        labelText: 'Server address',
                        hintText: 'wss://visionqc.tavesglobal.com',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _cameraId,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        labelText: 'Camera ID',
                        hintText: 'cam_xxxxxxxx',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _apiKey,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        labelText: 'API key',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 12),
                    OutlinedButton.icon(
                      onPressed: _busy ? null : _connectManually,
                      icon: const Icon(Icons.cloud_upload),
                      label: const Text('Connect'),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
