import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

/// Client for the VisionQC relay server (`server/app.py`), reachable through
/// the Cloudflare tunnel in production.
///
/// The phone registers as a camera: connect to `/ws/camera/{id}`, send the
/// API key as the first JSON message, then stream JPEG frames as binary.
/// The server replies with `assigned_model` (which model the dashboards
/// expect this camera to feed).
class RelayClient {
  RelayClient({
    required this.serverUrl,
    required this.cameraId,
    required this.apiKey,
  });

  final String serverUrl;
  final String cameraId;
  final String apiKey;

  WebSocket? _socket;
  String? assignedModel;
  final StreamController<String> _events = StreamController.broadcast();

  /// Human-readable connection/status updates.
  Stream<String> get events => _events.stream;

  bool get connected => _socket != null;

  /// `https://host` -> `wss://host/ws/camera/<id>` (and http/ws accepted).
  static Uri cameraUri(String serverUrl, String cameraId) {
    final base = serverUrl.trim();
    if (base.isEmpty) {
      throw const RelayException('Server address is empty');
    }
    final uri = Uri.parse(base);
    final scheme = switch (uri.scheme) {
      'https' => 'wss',
      'http' => 'ws',
      'wss' || 'ws' => uri.scheme,
      _ => throw RelayException('Unsupported address: ${uri.scheme}://...'),
    };
    return uri.replace(
      scheme: scheme,
      path: '/ws/camera/$cameraId',
      query: '',
    );
  }

  Future<void> connect({
    Duration timeout = const Duration(seconds: 12),
  }) async {
    await close();
    final uri = cameraUri(serverUrl, cameraId);
    final socket = await WebSocket.connect(uri.toString()).timeout(timeout);
    _socket = socket;
    socket.add(jsonEncode({'key': apiKey}));
    _events.add('connected');
    socket.listen(
      (data) {
        try {
          final message = jsonDecode(data as String) as Map<String, dynamic>;
          if (message['type'] == 'assigned_model') {
            assignedModel = message['model_version'] as String?;
            _events.add(assignedModel == null
                ? 'connected · no model assigned yet'
                : 'connected · model $assignedModel');
          }
        } catch (_) {
          // Non-JSON server chatter is not fatal.
        }
      },
      onDone: () {
        final code = socket.closeCode;
        _socket = null;
        final reason = switch (code) {
          4403 => ' (wrong API key)',
          4404 => ' (camera not registered)',
          _ => code == null ? '' : ' (code $code)',
        };
        _events.add('disconnected$reason');
      },
      onError: (Object error) {
        _socket = null;
        _events.add('error: $error');
      },
      cancelOnError: true,
    );
  }

  void sendFrame(Uint8List jpeg) {
    _socket?.add(jpeg);
  }

  Future<void> close() async {
    final socket = _socket;
    _socket = null;
    await socket?.close();
  }

  void dispose() {
    unawaited(close());
    _events.close();
  }
}

class RelayException implements Exception {
  const RelayException(this.message);

  final String message;

  @override
  String toString() => message;
}
