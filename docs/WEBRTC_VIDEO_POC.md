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


## Implemented PoC slice (2026-11-12)

The isolated branch now contains the first end-to-end implementation slice:

- `backend/agent/webrtc_video.py`: starts a locally managed MediaMTX process, validates its generated configuration, and starts one FFmpeg RTSP publisher per active local ADB device.
- The FFmpeg bridge reads raw H.264 from stdin and uses `-c:v copy`; it does not re-encode.
- A bounded, non-blocking queue and a dedicated writer thread isolate FFmpeg backpressure from the Scrcpy reader and existing WebSocket fan-out. If the queue fills or FFmpeg exits, only the optional WebRTC publisher is disabled.
- The existing local ADB hub sends one cached decodable GOP to the publisher, then subsequent live H.264 batches.
- `POST /api/devices/{device_id}/webrtc` acquires an authenticated WebRTC viewer reference and returns the generated WHEP URL; `DELETE` releases it. Both are restricted to owner/admin users and local ADB USB devices.
- `WebRTCPlayer.tsx` negotiates WHEP and renders through a native browser video element. The Live panel has an explicit WebSocket / WebRTC PoC selector; WebSocket remains the default and fallback.
- MediaMTX is configured to bind RTSP, WHEP and ICE UDP listeners to loopback only; unused RTMP, HLS, SRT, API, metrics, pprof and MoQ listeners are disabled. The generated stream path is random per producer. Do not change this to a LAN bind without adding proper WHEP authentication / proxying and ICE candidate configuration.

### Windows local setup

1. Download the Windows MediaMTX executable from the official MediaMTX releases and place it in a stable local tools directory.
2. Install a Windows FFmpeg build that includes the RTSP muxer.
3. Configure the backend process environment before starting Uvicorn:

```powershell
$env:SMART_AI_WEBRTC_ENABLED = "1"
$env:MEDIAMTX_PATH = "E:\\tools\\mediamtx\\mediamtx.exe"
$env:FFMPEG_PATH = "E:\\tools\\ffmpeg\\bin\\ffmpeg.exe"
# Optional ports (defaults shown)
$env:SMART_AI_MEDIAMTX_RTSP_PORT = "8554"
$env:SMART_AI_MEDIAMTX_WEBRTC_PORT = "8889"
$env:SMART_AI_MEDIAMTX_UDP_PORT = "8189"
```

4. Restart the backend, connect a local ADB USB Android device, open its live preview, then choose **WebRTC PoC** in the transport selector.
5. Keep the browser on the same Windows machine for this loopback-only PoC. For a browser on another machine, the signaling and ICE address design must be changed first.

### Current limitations / verification still required

- This branch has not yet been built or run against the user's Windows machine and physical Android device. The implementation is committed but runtime acceptance is pending.
- Scrcpy raw H.264 does not contain capture timestamps. FFmpeg is given the configured Scrcpy max FPS as the input frame rate; actual frame pacing and glass-to-glass latency must be measured.
- WebRTC acquires the shared Scrcpy hub through backend reference counting. It does not open a duplicate H.264 WebSocket payload stream per tab. Existing WebSocket viewers and WebRTC viewers can coexist on the same producer.
- WHEP HTTP signaling and ICE UDP must both work locally. If the offer succeeds but media does not flow, inspect the SDP ICE candidates and Windows Firewall UDP rule for the configured ICE port.
- The generated MediaMTX config is validated with `mediamtx --validate-conf=...` before the server starts.
