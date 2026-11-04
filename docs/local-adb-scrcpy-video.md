# Local ADB USB live video: Scrcpy

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

Restart the Smart-AI-Bot backend after placing the file.

## Runtime behavior

For an attached `adb_usb` device, the backend:

1. Pushes the pinned server to `/data/local/tmp/scrcpy-server.jar`.
2. Creates a device-specific ADB forward using a random Scrcpy SCID and host port.
3. Starts Scrcpy Server with video enabled, audio/control disabled, H.264 and
   `raw_stream=true`.
4. Relays raw Annex-B H.264 bytes through the existing
   `/v1/devices/{device_id}/live` WebSocket.
5. Closes the socket, ADB forward and host process when the viewer disconnects.

The existing H.264 WebCodecs/MSE frontend is reused. A failed Scrcpy startup
closes only that USB device's video WebSocket; Auto mode can fall back to the
existing JPEG screenshot path. It does not mark the device offline.

## First-device validation

1. Connect and authorize the Android phone with USB debugging.
2. Confirm `adb devices -l` shows the serial in `device` state.
3. Attach it in Smart-AI-Bot as Local ADB USB.
4. Open Live view and select Auto or Video.
5. Confirm the status says `Local USB · Scrcpy H.264`.
6. Change screens repeatedly and confirm frames update without screenshot polling.
7. Close the preview and verify the host ADB forward is removed.

If startup fails, inspect backend logs for `Scrcpy startup failed`. The most
common first-run issue is a missing or version-mismatched server asset.

## Scope / regression requirements

- Only the `adb_usb` branch uses Scrcpy.
- Portal continues using its existing provider WebSocket and `video/start` RPC.
- ADB Bridge, Browser USB, HDC Bridge and iOS Bridge are not redirected to Scrcpy.
- Device control remains on the existing Local ADB RPC implementation.