import 'dart:async';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';

import '../services/frame_encoding.dart';
import '../services/relay_client.dart';

/// Stream the phone camera to the VisionQC relay server.
///
/// Frames are sampled at ~5.5 fps (adaptive: slower devices back off
/// automatically) and sent as JPEG over the authenticated camera WebSocket.
/// The relay forwards them to dashboards; inference runs on the dashboard
/// side, so this screen shows connection state and frames sent.
class RelayStreamScreen extends StatefulWidget {
  const RelayStreamScreen({super.key, required this.client});

  final RelayClient client;

  @override
  State<RelayStreamScreen> createState() => _RelayStreamScreenState();
}

class _RelayStreamScreenState extends State<RelayStreamScreen> {
  /// Steady sampling rate: ~5.5 fps keeps the dashboard live while leaving
  /// the phone (encode) and the relay link plenty of headroom.
  static const _targetInterval = Duration(milliseconds: 180);

  Duration _interval = _targetInterval;
  CameraController? _controller;
  StreamSubscription<String>? _statusSub;
  bool _streaming = false;
  DateTime _lastSent = DateTime.fromMillisecondsSinceEpoch(0);
  int _framesSent = 0;
  String? _error;
  String _status = '';

  @override
  void initState() {
    super.initState();
    _status = widget.client.connected ? 'connected' : 'disconnected';
    _statusSub = widget.client.events.listen((event) {
      if (mounted) setState(() => _status = event);
    });
    _initCamera();
  }

  @override
  void dispose() {
    _statusSub?.cancel();
    _controller?.dispose();
    widget.client.dispose();
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
      if (!mounted) return;
      setState(() => _controller = controller);
      // Scanned from a pairing QR: go live without another tap.
      await _toggleStreaming();
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
    if (now.difference(_lastSent) < _interval) return;
    final watch = Stopwatch()..start();
    final jpeg = encodeCameraFrameJpeg(frame);
    watch.stop();
    _lastSent = now;
    if (jpeg == null) return;
    widget.client.sendFrame(jpeg);
    setState(() => _framesSent += 1);
    // Adaptive backoff: if encoding a frame takes longer than the target
    // interval, space frames out so we never lag behind the phone; recover
    // to the target rate as soon as a frame is quick again.
    _interval = watch.elapsed > _targetInterval
        ? watch.elapsed * 1.3
        : _targetInterval;
  }

  @override
  Widget build(BuildContext context) {
    final controller = _controller;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Cloud stream'),
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
                  : CameraPreview(controller),
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
                  Card(
                    child: ListTile(
                      leading: Icon(
                        widget.client.connected
                            ? Icons.cloud_done
                            : Icons.cloud_off,
                        color: widget.client.connected
                            ? Colors.green
                            : Colors.grey,
                      ),
                      title: Text(_status),
                      subtitle: Text(
                        '${widget.client.serverUrl}\n'
                        'camera ${widget.client.cameraId}',
                        style: const TextStyle(fontSize: 11),
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  FilledButton.icon(
                    onPressed: controller == null ? null : _toggleStreaming,
                    icon: Icon(_streaming ? Icons.stop : Icons.play_arrow),
                    label: Text(
                      _streaming ? 'Stop streaming' : 'Start streaming',
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Streaming runs only while this screen is open. Frames go '
                    'to your VisionQC relay server for dashboards to inspect.',
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
