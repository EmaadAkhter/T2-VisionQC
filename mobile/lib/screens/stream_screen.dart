import 'dart:async';
import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';

import '../services/edge_client.dart';
import '../services/frame_encoding.dart';

/// Continuous camera stream to the edge workstation.
///
/// Frames are sampled (default every 1.2 s) rather than sent at full camera
/// rate: the edge analyses what the phone sees and returns the verdict, score
/// and heatmap. Streaming runs only while this screen is open.
class StreamScreen extends StatefulWidget {
  const StreamScreen({
    super.key,
    required this.edge,
    required this.orgId,
    required this.orgName,
  });

  final EdgeClient edge;
  final String orgId;
  final String orgName;

  @override
  State<StreamScreen> createState() => _StreamScreenState();
}

class _StreamScreenState extends State<StreamScreen> {
  static const _minInterval = Duration(milliseconds: 1200);

  CameraController? _controller;
  bool _streaming = false;
  bool _inFlight = false;
  DateTime _lastSent = DateTime.fromMillisecondsSinceEpoch(0);
  int _framesSent = 0;
  InspectionResult? _result;
  String? _error;
  Uint8List? _lastFrameJpeg;

  @override
  void initState() {
    super.initState();
    _initCamera();
  }

  @override
  void dispose() {
    _controller?.dispose();
    super.dispose();
  }

  Future<void> _initCamera() async {
    try {
      final cameras = await availableCameras();
      if (cameras.isEmpty) {
        setState(() => _error = 'No camera found on this device.');
        return;
      }
      final camera = cameras.firstWhere(
        (c) => c.lensDirection == CameraLensDirection.back,
        orElse: () => cameras.first,
      );
      final controller = CameraController(
        camera,
        ResolutionPreset.medium,
        enableAudio: false,
        imageFormatGroup: ImageFormatGroup.yuv420,
      );
      await controller.initialize();
      setState(() => _controller = controller);
    } catch (error) {
      setState(() => _error = 'Camera error: $error');
    }
  }

  Future<void> _toggleStreaming() async {
    final controller = _controller;
    if (controller == null) return;
    if (_streaming) {
      await controller.stopImageStream();
      setState(() => _streaming = false);
      return;
    }
    try {
      await controller.startImageStream(_onFrame);
      setState(() {
        _streaming = true;
        _error = null;
      });
    } catch (error) {
      setState(() => _error = 'Could not start stream: $error');
    }
  }

  void _onFrame(CameraImage frame) {
    final now = DateTime.now();
    if (_inFlight || now.difference(_lastSent) < _minInterval) return;
    _inFlight = true;
    _lastSent = now;
    unawaited(_processFrame(frame));
  }

  Future<void> _processFrame(CameraImage frame) async {
    try {
      final jpeg = encodeCameraFrameJpeg(frame);
      if (jpeg == null) return;
      _lastFrameJpeg = jpeg;
      final result = await widget.edge.inspect(jpeg);
      if (!mounted) return;
      setState(() {
        _result = result;
        _framesSent += 1;
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      _inFlight = false;
    }
  }

  // -------------------------------------------------------------------- ui

  Color _verdictColor(String verdict) {
    switch (verdict) {
      case 'PASS':
        return const Color(0xFF16A34A);
      case 'FAIL':
        return const Color(0xFFDC2626);
      default:
        return const Color(0xFFD97706);
    }
  }

  @override
  Widget build(BuildContext context) {
    final controller = _controller;
    return Scaffold(
      appBar: AppBar(
        title: Text('Live — ${widget.orgName}'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: Center(
              child: Text(
                'sent $_framesSent',
                style: const TextStyle(fontSize: 12, color: Colors.white70),
              ),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            flex: 3,
            child: Container(
              color: Colors.black,
              width: double.infinity,
              child: controller == null || !controller.value.isInitialized
                  ? const Center(
                      child: CircularProgressIndicator(color: Colors.white),
                    )
                  : (_streaming && _lastFrameJpeg != null
                      ? Image.memory(_lastFrameJpeg!, fit: BoxFit.contain)
                      : CameraPreview(controller)),
            ),
          ),
          Expanded(
            flex: 2,
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (_error != null)
                    Text(_error!, style: const TextStyle(color: Colors.red)),
                  if (_result != null) ...[
                    Row(
                      children: [
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 14,
                            vertical: 6,
                          ),
                          decoration: BoxDecoration(
                            color: _verdictColor(_result!.verdict),
                            borderRadius: BorderRadius.circular(20),
                          ),
                          child: Text(
                            _result!.verdict,
                            style: const TextStyle(
                              color: Colors.white,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Text(
                          'score ${_result!.score.toStringAsFixed(2)} · '
                          '${_result!.latencyMs} ms · ${_result!.certainty}',
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(_result!.explanation),
                    const SizedBox(height: 4),
                    Text(
                      'Inspection ${_result!.uid}',
                      style: const TextStyle(fontSize: 11, color: Colors.black45),
                    ),
                    if (_result!.heatmapBytes != null) ...[
                      const SizedBox(height: 8),
                      ClipRRect(
                        borderRadius: BorderRadius.circular(8),
                        child: Image.memory(
                          _result!.heatmapBytes!,
                          height: 150,
                          fit: BoxFit.contain,
                        ),
                      ),
                    ],
                  ] else
                    const Text(
                      'Start streaming, then point the camera at a unit. '
                      'The edge machine analyses frames and returns the verdict.',
                      style: TextStyle(color: Colors.black54),
                    ),
                  const SizedBox(height: 16),
                  FilledButton.icon(
                    onPressed: controller == null ? null : _toggleStreaming,
                    icon: Icon(_streaming ? Icons.stop : Icons.play_arrow),
                    label: Text(_streaming ? 'Stop streaming' : 'Start streaming'),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Streaming is active only while this screen is open. '
                    'Frames go to the edge workstation on this network; '
                    'nothing is uploaded to the cloud.',
                    style: TextStyle(fontSize: 11, color: Colors.black38),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
