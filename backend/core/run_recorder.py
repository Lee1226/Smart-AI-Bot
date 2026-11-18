"""Records a run's device screen via ADB screenrecord (local devices only).

We record ON-DEVICE mp4 segments (`adb shell screenrecord <file>`) rather than
streaming raw H.264 to stdout: the on-device encoder/muxer writes correct frame
timestamps, so playback runs at real-time speed (a raw elementary stream has no
timestamps, which made replay play slow). screenrecord caps at ~3 min/segment,
so we chain segments and concat them with ffmpeg when the run ends.

Remote devices (no local ADB) are skipped — the per-step screenshot replay
covers those."""
import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

REC_DIR = Path(__file__).parent.parent / "data" / "recordings"


def recording_directory(settings: Optional[dict] = None) -> Path:
    configured = (settings or {}).get("recording_storage_dir") or str(REC_DIR)
    path = Path(configured).expanduser().resolve()
    if path == Path(path.anchor):
        raise ValueError("Recording directory cannot be a filesystem root")
    return path


def cleanup_expired_recordings(directory: Path, retention_days: int, owned_run_ids=None) -> int:
    cutoff = datetime.now().timestamp() - max(1, int(retention_days)) * 86400
    removed = 0
    if not directory.exists():
        return 0
    for path in directory.glob("*.mp4"):
        try:
            if owned_run_ids is not None and path.stem not in owned_run_ids:
                continue
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            logger.warning("Could not remove expired recording %s", path, exc_info=True)
    return removed


def _adb_path() -> Optional[str]:
    found = shutil.which("adb")
    if found:
        return found
    fallback = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
    return fallback if os.path.exists(fallback) else None


def _single_adb_serial() -> Optional[str]:
    """Return the serial only when exactly one device is attached (unambiguous)."""
    adb = _adb_path()
    if not adb:
        return None
    try:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    serials = [l.split()[0] for l in out.splitlines()[1:] if len(l.split()) >= 2 and l.split()[1] == "device"]
    return serials[0] if len(serials) == 1 else None


def recording_path(run_id: str, directory: Optional[Path] = None) -> Path:
    return (directory or REC_DIR) / f"{run_id}.mp4"


def has_recording(run_id: str, directory: Optional[Path] = None) -> bool:
    p = recording_path(run_id, directory)
    return p.exists() and p.stat().st_size > 0


async def _run(*args: str, timeout: Optional[float] = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except Exception:
        if proc.returncode is None:
            proc.kill()


class RunRecorder:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.serial: Optional[str] = None
        self.rec_dir: Path = REC_DIR
        self._task: Optional[asyncio.Task] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._stop = asyncio.Event()
        self._device_files: List[str] = []
        # adb_usb uses the already-running shared Scrcpy stream. The legacy
        # on-device screenrecord path remains unchanged for other local modes.
        self._shared_queue = None
        self._shared_release = None
        self._shared_task: Optional[asyncio.Task] = None
        self._shared_proc: Optional[asyncio.subprocess.Process] = None
        self._shared_stop = asyncio.Event()
        self._shared_stderr_task: Optional[asyncio.Task] = None
        self._shared_error_lines: List[str] = []

    async def start(self, device_id: Optional[str] = None) -> bool:
        # Recording is controlled by the account that owns this run.
        try:
            from db.database import AsyncSessionLocal
            from sqlalchemy import select
            from db.models import TestRun
            from core.settings_store import load_settings
            async with AsyncSessionLocal() as db:
                run = await db.get(TestRun, self.run_id)
                owned_run_ids = set((await db.execute(select(TestRun.id).where(TestRun.account_id == (run.account_id if run else "")))).scalars().all())
            if run is not None:
                settings = await load_settings(run.account_id or "")
                if not settings.get("recording_auto_enabled", True):
                    logger.info("[rec:%s] automatic recording disabled by account settings", self.run_id)
                    return False
                self.rec_dir = recording_directory(settings)
                self.rec_dir.mkdir(parents=True, exist_ok=True)
                cleanup_expired_recordings(self.rec_dir, settings.get("recording_retention_days", 30), owned_run_ids)
        except Exception as exc:
            logger.warning("[rec:%s] could not read recording settings; using default enabled: %s", self.run_id, exc)
        if device_id:
            from db.database import AsyncSessionLocal
            from db.models import Device
            async with AsyncSessionLocal() as db:
                device = await db.get(Device, device_id)
            if device is None or device.connection_type in {"browser_usb", "adb_bridge", "hdc_bridge", "ios_bridge"}:
                # USB belongs to a browser/bridge on another host. Never record
                # an unrelated server-side phone for that registered device.
                return False
            if device.connection_type == "adb_usb":
                return await self._start_shared_scrcpy(device_id)
        self.serial = _single_adb_serial()
        if not self.serial:
            return False
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._loop())
        logger.info("[rec:%s] recording via adb %s", self.run_id, self.serial)
        return True

    async def _start_shared_scrcpy(self, device_id: str) -> bool:
        """Record the existing adb_usb Scrcpy producer without starting another one."""
        from agent import local_adb

        serial = local_adb.serial_for(device_id)
        if not serial:
            logger.warning("[rec:%s] adb_usb device has no active serial binding; recording skipped", self.run_id)
            return False

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("[rec:%s] FFmpeg not found on PATH; shared-stream recording skipped", self.run_id)
            return False

        try:
            from routers.live import subscribe_local_adb_recorder
            queue, release = await subscribe_local_adb_recorder(device_id, serial)
            self._shared_queue = queue
            self._shared_release = release
            self.serial = serial

            partial = self.rec_dir / f"{self.run_id}.partial.mp4"
            partial.unlink(missing_ok=True)
            self._shared_proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
                "-fflags", "+genpts",
                "-f", "h264", "-r", "30", "-i", "pipe:0",
                "-an", "-c:v", "copy", "-movflags", "+faststart",
                str(partial),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            if self._shared_proc.stdin is None:
                raise RuntimeError("FFmpeg stdin pipe was not created")

            self._shared_stop.clear()
            self._shared_error_lines = []
            self._shared_stderr_task = asyncio.create_task(self._drain_shared_ffmpeg_stderr())
            self._shared_task = asyncio.create_task(self._shared_record_loop(partial))
            logger.info(
                "[rec:%s] shared Scrcpy recording started: device=%s serial=%s",
                self.run_id, device_id, serial,
            )
            return True
        except Exception as exc:
            logger.warning("[rec:%s] could not start shared Scrcpy recording: %s", self.run_id, exc)
            if self._shared_release:
                try:
                    await self._shared_release()
                except Exception:
                    pass
            self._shared_queue = None
            self._shared_release = None
            proc = self._shared_proc
            self._shared_proc = None
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()
            return False

    async def _drain_shared_ffmpeg_stderr(self) -> None:
        proc = self._shared_proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                message = line.decode("utf-8", errors="replace").strip()
                if message:
                    self._shared_error_lines.append(message)
                    if len(self._shared_error_lines) > 30:
                        self._shared_error_lines.pop(0)
        except (asyncio.CancelledError, OSError):
            pass

    async def _shared_record_loop(self, partial: Path) -> None:
        proc = self._shared_proc
        queue = self._shared_queue
        try:
            if proc is None or proc.stdin is None or queue is None:
                raise RuntimeError("Shared recorder was not initialized")

            while not self._shared_stop.is_set():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if proc.returncode is not None:
                        raise RuntimeError(f"FFmpeg exited unexpectedly ({proc.returncode})")
                    continue
                # The shared hub uses private object markers for GOP bootstrap
                # boundaries and None for Annex-B flush points. FFmpeg only
                # needs the actual elementary-stream bytes.
                if isinstance(item, bytes) and item:
                    proc.stdin.write(item)
                    await proc.stdin.drain()

            # Stop accepting new stream data, then drain what was already queued.
            while not queue.empty():
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(item, bytes) and item:
                    proc.stdin.write(item)
                    await proc.stdin.drain()

            proc.stdin.close()
            try:
                await proc.stdin.wait_closed()
            except (AttributeError, BrokenPipeError, ConnectionResetError):
                pass
            await asyncio.wait_for(proc.wait(), timeout=20)
            if proc.returncode != 0:
                details = " | ".join(self._shared_error_lines[-8:])
                raise RuntimeError(f"FFmpeg exited with code {proc.returncode}: {details}")

            final_path = recording_path(self.run_id, self.rec_dir)
            if not partial.exists() or partial.stat().st_size <= 0:
                raise RuntimeError("FFmpeg finished but produced an empty MP4")
            partial.replace(final_path)
            logger.info(
                "[rec:%s] shared Scrcpy MP4 saved: %s (%d bytes)",
                self.run_id, final_path.name, final_path.stat().st_size,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[rec:%s] shared Scrcpy recording failed: %s", self.run_id, exc)
            if partial.exists():
                try:
                    partial.unlink()
                except OSError:
                    pass
        finally:
            if proc and proc.returncode is None:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:
                    pass
            if self._shared_release:
                try:
                    await self._shared_release()
                except Exception:
                    logger.debug("[rec:%s] shared stream release failed", self.run_id, exc_info=True)
            if self._shared_stderr_task:
                try:
                    await asyncio.wait_for(self._shared_stderr_task, timeout=2)
                except Exception:
                    self._shared_stderr_task.cancel()
            self._shared_queue = None
            self._shared_release = None
            self._shared_proc = None
            self._shared_task = None

    async def _stop_shared_scrcpy(self) -> None:
        self._shared_stop.set()
        task = self._shared_task
        if task:
            try:
                await asyncio.wait_for(task, timeout=25)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _loop(self) -> None:
        adb = _adb_path()
        n = 0
        while not self._stop.is_set():
            devfile = f"/sdcard/smartrec_{self.run_id}_{n}.mp4"
            self._proc = await asyncio.create_subprocess_exec(
                adb, "-s", self.serial, "shell", "screenrecord", "--time-limit", "180", devfile,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await self._proc.wait()
            self._device_files.append(devfile)
            n += 1

    async def stop(self) -> None:
        if self._shared_task is not None:
            await self._stop_shared_scrcpy()
            return
        if self._task is None:
            return
        self._stop.set()
        # SIGINT lets the on-device screenrecord finalize the current mp4 (write
        # the moov atom) so the last segment is playable.
        adb = _adb_path()
        await _run(adb, "-s", self.serial, "shell", "pkill", "-INT", "screenrecord", timeout=5)
        try:
            await asyncio.wait_for(self._task, timeout=15)
        except Exception:
            pass
        await self._finalize()

    async def _finalize(self) -> None:
        adb = _adb_path()
        local_segs: List[Path] = []
        for i, dev in enumerate(self._device_files):
            local = self.rec_dir / f"{self.run_id}_seg{i}.mp4"
            await _run(adb, "-s", self.serial, "pull", dev, str(local), timeout=30)
            if local.exists() and local.stat().st_size > 0:
                local_segs.append(local)
            await _run(adb, "-s", self.serial, "shell", "rm", "-f", dev, timeout=10)

        if not local_segs:
            return
        mp4 = recording_path(self.run_id, self.rec_dir)
        try:
            if len(local_segs) == 1:
                local_segs[0].replace(mp4)
            else:
                listfile = self.rec_dir / f"{self.run_id}_list.txt"
                listfile.write_text("".join(f"file '{s.name}'\n" for s in local_segs))
                await _run(
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
                    "-c", "copy", "-movflags", "+faststart", str(mp4), timeout=120,
                )
                listfile.unlink(missing_ok=True)
                for s in local_segs:
                    s.unlink(missing_ok=True)
            logger.info("[rec:%s] saved %s", self.run_id, mp4.name)
        except Exception as e:
            logger.warning("[rec:%s] finalize failed: %s", self.run_id, e)
