import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:image/image.dart' as img;

/// Encode one camera frame as a JPEG for streaming.
///
/// Shared by the edge and relay streaming screens; YUV420 is the common
/// Android format, BGRA covers iOS and some devices.
Uint8List? encodeCameraFrameJpeg(CameraImage frame, {int quality = 75}) {
  img.Image? converted;
  if (frame.format.group == ImageFormatGroup.yuv420) {
    converted = _yuv420ToImage(frame);
  } else if (frame.format.group == ImageFormatGroup.bgra8888) {
    converted = img.Image.fromBytes(
      width: frame.width,
      height: frame.height,
      bytes: frame.planes[0].bytes.buffer,
      order: img.ChannelOrder.bgra,
    );
  }
  if (converted == null) return null;
  return img.encodeJpg(converted, quality: quality);
}

img.Image _yuv420ToImage(CameraImage frame) {
  final width = frame.width;
  final height = frame.height;
  final out = img.Image(width: width, height: height);
  final yPlane = frame.planes[0];
  final uPlane = frame.planes[1];
  final vPlane = frame.planes[2];
  final uvRowStride = uPlane.bytesPerRow;
  final uvPixelStride = uPlane.bytesPerPixel ?? 1;

  for (var y = 0; y < height; y++) {
    for (var x = 0; x < width; x++) {
      final yIndex = y * yPlane.bytesPerRow + x;
      final uvIndex = (y >> 1) * uvRowStride + (x >> 1) * uvPixelStride;
      final yy = yPlane.bytes[yIndex];
      final uu = uPlane.bytes[uvIndex];
      final vv = vPlane.bytes[uvIndex];
      final r = (yy + 1.402 * (vv - 128)).round().clamp(0, 255);
      final g = (yy - 0.344136 * (uu - 128) - 0.714136 * (vv - 128))
          .round()
          .clamp(0, 255);
      final b = (yy + 1.772 * (uu - 128)).round().clamp(0, 255);
      out.setPixelRgb(x, y, r, g, b);
    }
  }
  return out;
}
