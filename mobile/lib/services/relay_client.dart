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

  /// `wss://host` -> `https://host` for REST calls (and ws/http accepted).
  static String httpBaseUrl(String serverUrl) {
    var base = serverUrl.trim();
    if (base.startsWith('wss://')) {
      base = 'https://${base.substring('wss://'.length)}';
    } else if (base.startsWith('ws://')) {
      base = 'http://${base.substring('ws://'.length)}';
    }
    while (base.endsWith('/')) {
      base = base.substring(0, base.length - 1);
    }
    return base;
  }

  /// Exchange a QR pairing token for camera credentials (single use).
  static Future<Map<String, dynamic>> claimPairing(
    String serverUrl,
    String token,
  ) async {
    const timeout = Duration(seconds: 12);
    final http = HttpClient();
    try {
      final request = await http
          .postUrl(Uri.parse('${httpBaseUrl(serverUrl)}/pairing/claim'))
          .timeout(timeout);
      request.headers.contentType = ContentType.json;
      request.write(jsonEncode({'token': token}));
      final response = await request.close().timeout(timeout);
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        throw const RelayException(
          'Pairing code rejected — ask the desktop for a fresh QR code.',
        );
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      http.close(force: true);
    }
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
