import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

/// Client for the local VisionQC edge workstation.
///
/// The phone talks directly to the edge over the factory LAN; frames are
/// analysed on the edge machine and never uploaded to the cloud.
class EdgeClient {
  EdgeClient(this.baseUrl);

  final String baseUrl;
  String? deviceToken;

  Uri _uri(String path) {
    final trimmed = baseUrl.endsWith('/')
        ? baseUrl.substring(0, baseUrl.length - 1)
        : baseUrl;
    return Uri.parse('$trimmed$path');
  }

  Future<Map<String, dynamic>> health() async {
    final response = await http.get(_uri('/health')).timeout(
          const Duration(seconds: 5),
        );
    if (response.statusCode != 200) {
      throw EdgeException('Edge health check failed (${response.statusCode})');
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<void> pair({required String code, required String name}) async {
    final response = await http
        .post(
          _uri('/pair'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'code': code, 'name': name}),
        )
        .timeout(const Duration(seconds: 10));
    if (response.statusCode != 200) {
      throw EdgeException(
        response.statusCode == 403
            ? 'Invalid pairing code. Check the code shown on the edge app.'
            : 'Pairing failed (${response.statusCode})',
      );
    }
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    deviceToken = body['token'] as String;
  }

  Future<InspectionResult> inspect(Uint8List jpegBytes) async {
    final token = deviceToken;
    if (token == null) {
      throw EdgeException('Device is not paired');
    }
    final response = await http
        .post(
          _uri('/inspect'),
          headers: {
            'Content-Type': 'application/octet-stream',
            'X-Device-Token': token,
          },
          body: jpegBytes,
        )
        .timeout(const Duration(seconds: 20));
    if (response.statusCode != 200) {
      throw EdgeException('Inspection failed (${response.statusCode})');
    }
    return InspectionResult.fromJson(
      jsonDecode(response.body) as Map<String, dynamic>,
    );
  }
}

class InspectionResult {
  InspectionResult({
    required this.uid,
    required this.verdict,
    required this.score,
    required this.certainty,
    required this.explanation,
    required this.latencyMs,
    this.heatmapJpegBase64,
  });

  final String uid;
  final String verdict;
  final double score;
  final String certainty;
  final String explanation;
  final int latencyMs;
  final String? heatmapJpegBase64;

  factory InspectionResult.fromJson(Map<String, dynamic> json) {
    return InspectionResult(
      uid: json['uid'] as String? ?? '',
      verdict: json['verdict'] as String? ?? 'REVIEW',
      score: (json['score'] as num?)?.toDouble() ?? 0,
      certainty: json['certainty'] as String? ?? '',
      explanation: json['explanation'] as String? ?? '',
      latencyMs: (json['latency_ms'] as num?)?.toInt() ?? 0,
      heatmapJpegBase64: json['heatmap_jpeg_b64'] as String?,
    );
  }

  Uint8List? get heatmapBytes {
    final data = heatmapJpegBase64;
    if (data == null || data.isEmpty) return null;
    return base64Decode(data);
  }
}

class EdgeException implements Exception {
  EdgeException(this.message);
  final String message;

  @override
  String toString() => message;
}
