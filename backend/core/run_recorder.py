"""Task-scoped recording from the shared Scrcpy audio/video hub.

The recorder subscribes to the existing per-device Scrcpy producer. Browser
preview and recording therefore share one device-side Scrcpy process, while
each consumer has independent bounded queues.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

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
            run_stem = path.name[:-len(".part.mp4")] if path.name.endswith(".part.mp4") else path.stem
            if owned_run_ids is not None and run_stem not in owned_run_ids:
                continue
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            logger.warning("Could not remove expired recording %s", path, exc_info=True)
    return removed


def recording_path(run_id: str, directory: Optional[Path] = None) -> Path:
    return (directory or REC_DIR) / f"{run_id}.mp4"


def has_recording(run_id: str, directory: Optional[Path] = None) -> bool:
    path = recording_path(run_id, directory)
    return path.exists() and path.stat().st_size > 0


class RunRecorder:
    """Own one task's subscription and MP4 output; never owns the Scrcpy hub."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.serial: Optional[str] = None
        self.rec_dir: Path = REC_DIR
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._video_queue: asyncio.Queue | None = None
        self._audio_queue: asyncio.Queue | None = None
        self._release = None
        self._recording_error: str | None = None
        self._started = False

    async def start(self, device_id: Optional[str] = None) -> bool:
        if not device_id:
            return False
        try:
            from db.database import AsyncSessionLocal
            from db.models import Device, TestRun
            from sqlalchemy import select
            from core.settings_store import load_settings

            async with AsyncSessionLocal() as db:
                run = await db.get(TestRun, self.run_id)
                if run is None:
                    logger.warning("[rec:%s] run record not found; recording skipped", self.run_id)
                    return False
                device = await db.get(Device, device_id)
                owned_run_ids = set((
                    await db.execute(
                        select(TestRun.id).where(TestRun.account_id == run.account_id)
                    )
                ).scalars().all())

            settings = await load_settings(run.account_id or "")
            if not settings.get("recording_auto_enabled", True):
                logger.info("[rec:%s] automatic recording disabled by account settings", self.run_id)
                return False
            self.rec_dir = recording_directory(settings)
            self.rec_dir.mkdir(parents=True, exist_ok=True)
            cleanup_expired_recordings(
                self.rec_dir, settings.get("recording_retention_days", 30), owned_run_ids
            )

            # Only server-attached ADB USB devices are eligible for this
            # Scrcpy transport. Never fall back to an arbitrary attached phone.
            if device is None or device.connection_type != "adb_usb" or not device.adb_serial:
                logger.info(
                    "[rec:%s] device is not a bound local ADB USB device; recording skipped",
                    self.run_id,
                )
                return False
            self.serial = device.adb_serial

            try:
                import av
                if not hasattr(av.container.OutputContainer, "add_mux_stream"):
                    raise RuntimeError("PyAV 17 or newer is required")
            except Exception as exc:
                raise RuntimeError(f"MP4 audio/video muxer dependency unavailable: {exc}") from exc

            from routers.live import subscribe_local_adb_recorder
            video_queue, audio_queue, metadata, release = await subscribe_local_adb_recorder(
                device_id, self.serial
            )
            video_meta = metadata.get("video") if isinstance(metadata, dict) else None
            width = getattr(video_meta, "width", None)
            height = getattr(video_meta, "height", None)
            if not width or not height:
                await release()
                raise RuntimeError("Scrcpy did not report valid video dimensions")

            self._video_queue = video_queue
            self._audio_queue = audio_queue
            self._release = release
            self._stop = asyncio.Event()
            self._recording_error = None
            self._task = asyncio.create_task(
                self._consume_av(int(width), int(height)),
                name=f"run-recorder-{self.run_id}",
            )
            self._started = True
            logger.info(
                "[rec:%s] subscribed to shared Scrcpy A/V: serial=%s size=%sx%s",
                self.run_id, self.serial, width, height,
            )
            return True
        except Exception as exc:
            logger.warning("[rec:%s] could not start A/V recording: %s", self.run_id, exc)
            self._recording_error = str(exc)
            if self._release is not None:
                try:
                    await self._release()
                except Exception:
                    logger.debug("[rec:%s] subscription cleanup failed", self.run_id, exc_info=True)
                self._release = None
            return False

    async def _consume_av(self, width: int, height: int) -> None:
        from core.scrcpy_mp4_muxer import ScrcpyMp4Muxer

        final_path = recording_path(self.run_id, self.rec_dir)
        temp_path = self.rec_dir / f"{self.run_id}.part.mp4"
        temp_path.unlink(missing_ok=True)
        muxer = ScrcpyMp4Muxer(temp_path, width, height)
        mux_lock = asyncio.Lock()
        consumers: set[asyncio.Task] = set()

        async def consume(queue: asyncio.Queue, write_packet, stream_name: str) -> None:
            stop_quiet_since: float | None = None
            loop = asyncio.get_running_loop()
            while True:
                if self._stop.is_set() and queue.empty():
                    if stop_quiet_since is None:
                        stop_quiet_since = loop.time()
                    elif loop.time() - stop_quiet_since >= 0.5:
                        return
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue
                stop_quiet_since = None
                if item is None:
                    return
                if isinstance(item, dict) and item.get("error"):
                    raise RuntimeError(f"{stream_name} capture failed: {item['error']}")
                async with mux_lock:
                    await asyncio.to_thread(write_packet, item)

        try:
            if self._video_queue is None or self._audio_queue is None:
                raise RuntimeError("Recorder queues are not initialized")
            consumers = {
                asyncio.create_task(consume(self._video_queue, muxer.write_video, "video")),
                asyncio.create_task(consume(self._audio_queue, muxer.write_audio, "audio")),
            }
            done, pending = await asyncio.wait(
                consumers, return_when=asyncio.FIRST_EXCEPTION
            )
            failure = next((task.exception() for task in done if task.exception()), None)
            if failure is not None:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                raise failure
            await asyncio.gather(*pending)
            await asyncio.to_thread(muxer.close)
            os.replace(temp_path, final_path)
            logger.info("[rec:%s] saved audio/video recording: %s", self.run_id, final_path)
        except asyncio.CancelledError:
            for task in consumers:
                task.cancel()
            if consumers:
                await asyncio.gather(*consumers, return_exceptions=True)
            await asyncio.to_thread(muxer.abort)
            raise
        except Exception as exc:
            self._recording_error = str(exc)
            logger.warning("[rec:%s] audio/video recording incomplete: %s", self.run_id, exc)
            try:
                await asyncio.to_thread(muxer.abort)
            except Exception:
                logger.debug("[rec:%s] muxer abort failed", self.run_id, exc_info=True)
            temp_path.unlink(missing_ok=True)

    async def stop(self) -> None:
        if not self._started and self._release is None:
            return

        # Detach first so the shared hub stops enqueueing packets for this
        # recorder. Existing queued packets remain available for a clean drain.
        if self._release is not None:
            try:
                await self._release()
            except Exception:
                logger.warning("[rec:%s] failed to release shared Scrcpy subscription", self.run_id, exc_info=True)
            self._release = None

        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=20)
            except asyncio.TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                logger.warning("[rec:%s] recorder drain timed out", self.run_id)
            except Exception:
                logger.warning("[rec:%s] recorder task stopped with error", self.run_id, exc_info=True)
            self._task = None

        self._video_queue = None
        self._audio_queue = None
        self._started = False
        if self._recording_error:
            logger.warning("[rec:%s] recording not saved: %s", self.run_id, self._recording_error)
