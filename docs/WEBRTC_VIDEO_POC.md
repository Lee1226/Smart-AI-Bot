# WebRTC Video Pipeline PoC

## Goal

Validate whether a browser-native WebRTC receive path reduces live-screen latency and prevents the growing playback delay currently observed when several browser tabs watch one local ADB USB device.

This is an isolated experiment. The existing WebSocket + WebCodecs/Canvas path remains the default and is not removed.

## Current baseline (verified from the branch)

- `backend/agent/scrcpy_video.py` owns one Scrcpy server process and reads raw Annex-B H.264 from the ADB-forwarded TCP socket.
- `backend/routers/live.py` currently owns a per-device producer, parses Annex-B, caches SPS/PPS + an IDR GOP, and fans the stream out through a bounded queue per WebSocket viewer.
- `frontend/src/components/LivePanel.tsx` selects Canvas/WebGL WebCodecs or the jMuxer/MSE fallback for the local ADB USB source.
- The current browser playback path therefore has a custom WebSocket transport and a per-tab decode/render pipeline.

## Proposed PoC topology

```text
Android device
  └─ Scrcpy server (H.264, raw_stream=true)
       └─ one ScrcpyVideoSession per device
            └─ FFmpeg stdin (H.264 demux, stream copy; no video re-encode)
                 └─ RTSP publish over localhost
                      └─ MediaMTX
                           └─ WHEP / WebRTC
                                ├─ Browser tab A (<video>)
                                ├─ Browser tab B (<video>)
                                └─ Browser tab C (<video>)
```

MediaMTX is a separate native executable (Windows supported; no Docker required). FFmpeg is used only as a transport bridge from Scrcpy's raw H.264 byte stream to RTSP; use `-c:v copy` so the video is not re-encoded.

## Isolation and compatibility rules

1. First target only devices with `connection_type == "adb_usb"`.
2. Keep Portal, ADB bridge, Browser USB, HDC/HarmonyOS and iOS paths unchanged.
3. Keep the existing `/v1/devices/{device_id}/live` WebSocket route as the fallback.
4. Add an explicit feature flag; default it to disabled until the PoC passes local testing.
5. Do not create one Scrcpy process per browser tab. A device owns one Scrcpy producer and one publisher process; MediaMTX fans the published stream out to viewers.
6. On last viewer disconnect, stop the publisher and Scrcpy session after a short grace period; on device detach or backend shutdown, stop them immediately.
7. Do not expose MediaMTX's RTSP publishing port or unauthenticated control API to the LAN. Browser WHEP access must be constrained to the backend host / authenticated proxy before enabling remote access.

## PoC implementation sequence

### Phase 1 — media-server and publisher lifecycle

- Add Windows-friendly executable configuration for MediaMTX and FFmpeg via environment variables.
- Start MediaMTX once per backend process, with WebRTC and RTSP listeners bound to loopback for local PoC.
- Start one FFmpeg process per active local ADB device, feeding the Scrcpy H.264 stream through stdin and publishing to a unique RTSP path.
- Handle broken pipes, process exit, reconnect, cleanup and device detach.
- Keep process stdout/stderr drained to avoid Windows pipe deadlocks.

### Phase 2 — browser WHEP receiver

- Add a WebRTC player component using a browser `RTCPeerConnection` and MediaMTX's WHEP endpoint.
- Render through a native `<video autoplay muted playsInline>` element.
- Use a backend-issued short-lived playback credential or authenticated same-origin proxy; do not put the device's long-lived API token into an untrusted media URL.
- Add a per-device opt-in switch. Do not silently replace the current player.

### Phase 3 — metrics and comparison

Capture browser `RTCPeerConnection.getStats()` deltas, at minimum:
- `framesDecoded`, `framesDropped`, `framesPerSecond`
- `jitterBufferDelay`, `jitterBufferEmittedCount`
- `packetsLost`, `nackCount`, `pliCount`
- `bytesReceived`, `framesReceived`

Compare current WebSocket and WebRTC paths with the same device, resolution, frame rate and bitrate:
- one tab, then three tabs;
- static screen, continuous scrolling, and video playback;
- median and p95 glass-to-glass delay, frame rate, dropped frames, CPU and memory;
- synchronization spread between tabs.

The target of 100–300 ms end-to-end delay and less than 100 ms inter-tab skew is an acceptance target, not a guarantee of WebRTC.

## Ports (local PoC defaults)

- MediaMTX RTSP publish: `127.0.0.1:8554`
- MediaMTX WHEP/WebRTC HTTP: `127.0.0.1:8889`
- MediaMTX WebRTC UDP media: loopback/local test only; configure ICE candidates explicitly if testing across machines.

Do not assume the development Vite proxy automatically proxies WHEP/WebRTC media. The HTTP signaling path and UDP media path need separate treatment.

## Initial acceptance checklist

- [ ] MediaMTX starts and stops cleanly on Windows without Docker.
- [ ] FFmpeg can ingest the actual Scrcpy raw Annex-B stream from stdin and publish H.264 without re-encoding.
- [ ] One browser tab displays live video and recovers after publisher restart.
- [ ] Three tabs can subscribe simultaneously without starting additional Scrcpy sessions.
- [ ] Disconnecting one tab does not interrupt the others.
- [ ] Last-tab disconnect and device detach release FFmpeg, MediaMTX path resources and Scrcpy/ADB forward.
- [ ] Existing WebSocket playback remains available and unchanged.
- [ ] Performance comparison is recorded before deciding whether WebRTC becomes the default.

## Important implementation caveat

Scrcpy raw H.264 does not carry capture timestamps. FFmpeg must synthesize a frame cadence for the input; the chosen input frame rate must match the configured Scrcpy capture rate. This can affect pacing and measured latency. The first PoC must verify real output timing instead of assuming that switching to WebRTC alone removes upstream buffering.
