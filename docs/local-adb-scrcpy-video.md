# Local ADB USB live video and task recording: Scrcpy

This integration changes only devices whose `connection_type == "adb_usb"`.
Portal, ADB Bridge, Browser USB, HDC Bridge and iOS Bridge keep their existing
video providers and WebSocket paths.

## Pinned server version

The backend currently expects Scrcpy Server **4.0**. The client and server
versions must match exactly.

Download the official server asset from the Genymobile release:

https://github.com/Genymobile/scrcpy/releases/download/v4.0/scrcpy-server-v4.0

Save it as:

```text
backend/assets/scrcpy-server-v4.0
```

Create the directory if necessary. Alternatively, set `SCRCPY_SERVER_PATH`
to the absolute path of the downloaded file before starting the backend.

Do not use a server JAR from a different Scrcpy version.

## Windows PowerShell setup

Run from the repository root:

```powershell
New-Item -ItemType Directory -Force .\backend\assets | Out-Null
Invoke-WebRequest -Uri "https://github.com/Genymobile/scrcpy/releases/download/v4.0/scrcpy-server-v4.0" -OutFile ".\backend\assets\scrcpy-server-v4.0"
Get-Item .\backend\assets\scrcpy-server-v4.0
```

Install backend requirements and restart the Smart-AI-Bot backend:

```powershell
pip install -r .\backend\requirements.txt
```

PyAV 17 or newer is required for packet-level muxing. The requirement range
allows PyAV to select a compatible wheel for the active Python version.

## Runtime behavior

For an attached `adb_usb` device, the shared local ADB hub:

1. Pushes the pinned server to `/data/local/tmp/scrcpy-server.jar`.
2. Creates one device-specific ADB forward using a random Scrcpy SCID and host port.
3. Starts one Scrcpy Server process with H.264 video and AAC output audio enabled.
4. Opens the Scrcpy video socket first and the audio socket second. Both sockets
   belong to the same Scrcpy server process and carry codec metadata and device PTS.
5. Parses the framed media packets once. Browser viewers receive the same
   Annex-B H.264 payload format they already consume; task recorders receive
   independent bounded queues of timestamped video and audio packets.
6. Stops the producer only after the final viewer and recorder subscription leaves.

The browser WebSocket protocol and frontend decoder are unchanged. A slow or
failed recorder is isolated from preview queues. If audio capture is unavailable,
the recorder is marked incomplete and no silent/partial MP4 is published. The
browser preview continues; if the audio-enabled Scrcpy session cannot start,
the hub falls back to the established video-only preview session.

## Task recording

For a local ADB USB task with automatic recording enabled:

- `RunRecorder` subscribes to the existing per-device hub; it does not start a
  second Scrcpy process when preview is already active.
- Video and audio packets are muxed into one MP4 using their Scrcpy device PTS.
- H.264/AAC payloads are copied into the container; media is not decoded or
  re-encoded.
- The recorder owns its queues and output file. Releasing it does not stop a
  browser preview that still has subscribers.
- Capture, audio, queue or muxing errors are warnings and do not fail the test run.
- A recording is published only after the file contains both video and audio
  streams, codec configuration and media packets.

Android playback capture can be restricted by OS version or by the source app.
A valid audio stream must be confirmed on the actual phone and app under test.
HDC/HarmonyOS recording is not covered by this ADB USB implementation and
requires separate device validation.

## First-device validation

1. Connect and authorize an Android phone with USB debugging.
2. Confirm `adb devices -l` shows the serial in `device` state.
3. Attach it in Smart-AI-Bot as Local ADB USB.
4. Open Live view and select Auto or Video. Confirm the status says
   `Local USB · Scrcpy H.264`.
5. Start a test task while the preview is open. Confirm there is still only one
   Scrcpy server process for that device.
6. Play audio from the app during the task, then stop the task.
7. Download the resulting MP4 and verify picture, audible sound, duration and
   lip-sync/audio-sync. Also verify the browser preview stayed responsive.
8. Close the preview and verify the host ADB forward is removed.

If startup fails, inspect backend logs for `[SCRCPY MEDIA]` and
`[LIVE HUB]`. The most common first-run issue is a missing or
version-mismatched server asset.

## Scope / regression requirements

- Only the `adb_usb` branch uses this Scrcpy media hub.
- Portal continues using its existing provider WebSocket and `video/start` RPC.
- ADB Bridge, Browser USB, HDC Bridge and iOS Bridge are not redirected to Scrcpy.
- Device control remains on the existing Local ADB RPC implementation.
- Audio failure must never stop an otherwise working browser preview.
