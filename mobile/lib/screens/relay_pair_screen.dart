import 'package:flutter/material.dart';

import '../services/relay_client.dart';
import 'relay_stream_screen.dart';

/// Connect this phone to the VisionQC cloud relay as a camera.
///
/// The camera ID and API key are created on the relay
/// (`POST /cameras/register` with the dashboard token); the operator types
/// them here once. Works over any network through the Cloudflare tunnel.
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

  Future<void> _connect() async {
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
      await client.connect();
      if (!mounted) {
        client.dispose();
        return;
      }
      Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => RelayStreamScreen(client: client),
        ),
      );
    } catch (error) {
      client.dispose();
      setState(() => _error = '$error');
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
          _status = null;
        });
      }
    }
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
      appBar: AppBar(title: const Text('Cloud relay')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text(
                'Stream this phone camera to the VisionQC server. The '
                'camera ID and API key come from the server operator.',
                style: TextStyle(color: Colors.black54),
              ),
              const SizedBox(height: 16),
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
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(_error!, style: const TextStyle(color: Colors.red)),
              ],
              if (_status != null) ...[
                const SizedBox(height: 12),
                Text(_status!, style: const TextStyle(color: Colors.black54)),
              ],
              const SizedBox(height: 16),
              FilledButton.icon(
                onPressed: _busy ? null : _connect,
                icon: _busy
                    ? const SizedBox(
                        height: 18,
                        width: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.cloud_upload),
                label: const Text('Connect and open camera'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
