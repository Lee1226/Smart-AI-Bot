"""Task-scoped recording from the shared Scrcpy audio/video hub.

The recorder subscribes to the existing per-device Scrcpy producer. Browser
preview and recording therefore share one device-side Scrcpy process, while
each consumer has independent bounded queues.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
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
        self.saved = False
        self.status_message = "录像尚未启动"

    async def start(self, device_id: Optional[str] = None) -> bool:
        self.saved = False
        self.status_message = "录像启动失败：没有关联测试设备"
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
                    self.status_message = "录像未启动：测试运行记录不存在"
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
                self.status_message = "录像未启动：设置中已关闭自动录像"
                logger.info("[rec:%s] automatic recording disabled by account settings", self.run_id)
                return False
            self.rec_dir = recording_directory(settings)
            self.rec_dir.mkdir(parents=True, exist_ok=True)
            # A rerun must not expose a stale MP4 if the new capture fails.
            recording_path(self.run_id, self.rec_dir).unlink(missing_ok=True)
            (self.rec_dir / f"{self.run_id}.part.mp4").unlink(missing_ok=True)
            cleanup_expired_recordings(
                self.rec_dir, settings.get("recording_retention_days", 30), owned_run_ids
            )

            # Only server-attached ADB USB devices are eligible for this
            # Scrcpy transport. Never fall back to an arbitrary attached phone.
            if device is None or device.connection_type != "adb_usb" or not device.adb_serial:
                connection_type = getattr(device, "connection_type", "unknown") if device else "missing"
                self.status_message = (
                    "录像未启动：当前设备不是已绑定的本机 ADB USB 设备"
                    f"（connection_type={connection_type}）。当前录像链路仅支持 adb_usb。"
                )
                logger.warning(
                    "[rec:%s] device is not a bound local ADB USB device; recording skipped "
                    "(connection_type=%s, adb_serial=%s)",
                    self.run_id, connection_type, bool(getattr(device, "adb_serial", None)),
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
            self.status_message = "录像已启动，正在采集音视频"
            logger.info(
                "[rec:%s] subscribed to shared Scrcpy A/V: serial=%s size=%sx%s",
                self.run_id, self.serial, width, height,
            )
            return True
        except Exception as exc:
            logger.warning("[rec:%s] could not start A/V recording: %s", self.run_id, exc, exc_info=True)
            self._recording_error = str(exc)
            self.status_message = f"录像启动失败：{exc}"
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
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"rec-mux-{self.run_id[:8]}")
        loop = asyncio.get_running_loop()
        get_tasks: dict[str, asyncio.Task] = {}
        stats = {
            "video_packets": 0, "audio_packets": 0,
            "video_bytes": 0, "audio_bytes": 0,
            "batches": 0, "write_seconds": 0.0, "max_batch_seconds": 0.0,
            "max_video_queue": 0, "max_audio_queue": 0,
        }

        def write_batch(batch: list[tuple[str, object]]) -> float:
            started = time.perf_counter()
            for stream_name, packet in batch:
                if stream_name == "video":
                    muxer.write_video(packet)
                else:
                    muxer.write_audio(packet)
            return time.perf_counter() - started

        async def write_items(items: list[tuple[str, object]]) -> None:
            if not items:
                return
            items.sort(key=lambda entry: (
                0 if entry[1].kind == "configuration" else 1,
                -1 if entry[1].pts_us is None else entry[1].pts_us,
                0 if entry[0] == "video" else 1,
            ))
            elapsed = await loop.run_in_executor(executor, write_batch, items)
            stats["batches"] += 1
            stats["write_seconds"] += elapsed
            stats["max_batch_seconds"] = max(stats["max_batch_seconds"], elapsed)
            for stream_name, packet in items:
                if packet.kind != "configuration":
                    stats[f"{stream_name}_packets"] += 1
                    stats[f"{stream_name}_bytes"] += len(packet.payload)

        try:
            if self._video_queue is None or self._audio_queue is None:
                raise RuntimeError("Recorder queues are not initialized")
            queues = {"video": self._video_queue, "audio": self._audio_queue}
            for name, queue in queues.items():
                get_tasks[name] = asyncio.create_task(queue.get(), name=f"rec-{name}-get-{self.run_id[:8]}")
            idle_since: float | None = None

            while True:
                done, _ = await asyncio.wait(
                    set(get_tasks.values()), timeout=0.2,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                batch: list[tuple[str, object]] = []
                ended: set[str] = set()

                for name, task in list(get_tasks.items()):
                    if task not in done:
                        continue
                    item = task.result()
                    if item is None:
                        ended.add(name)
                        get_tasks.pop(name, None)
                    elif isinstance(item, dict) and item.get("error"):
                        raise RuntimeError(f"{name} capture failed: {item['error']}")
                    else:
                        batch.append((name, item))
                        get_tasks[name] = asyncio.create_task(
                            queues[name].get(), name=f"rec-{name}-get-{self.run_id[:8]}"
                        )

                # Drain both bounded queues in batches, amortizing executor
                # scheduling while preserving device PTS order within each batch.
                for name, queue in queues.items():
                    if name in ended or name not in get_tasks:
                        continue
                    for _ in range(64):
                        try:
                            item = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if item is None:
                            ended.add(name)
                            task = get_tasks.pop(name, None)
                            if task is not None:
                                task.cancel()
                            break
                        if isinstance(item, dict) and item.get("error"):
                            raise RuntimeError(f"{name} capture failed: {item['error']}")
                        batch.append((name, item))

                stats["max_video_queue"] = max(stats["max_video_queue"], queues["video"].qsize())
                stats["max_audio_queue"] = max(stats["max_audio_queue"], queues["audio"].qsize())
                await write_items(batch)

                if self._stop.is_set() and queues["video"].empty() and queues["audio"].empty():
                    if idle_since is None:
                        idle_since = loop.time()
                    elif loop.time() - idle_since >= 0.5:
                        break
                else:
                    idle_since = None

                if not get_tasks and queues["video"].empty() and queues["audio"].empty():
                    break

            for task in get_tasks.values():
                task.cancel()
            if get_tasks:
                await asyncio.gather(*get_tasks.values(), return_exceptions=True)
            get_tasks.clear()

            await loop.run_in_executor(executor, muxer.close)
            os.replace(temp_path, final_path)
            self.saved = True
            self.status_message = f"录像已保存：{final_path.name}"
            average_ms = stats["write_seconds"] * 1000 / stats["batches"] if stats["batches"] else 0.0
            logger.info(
                "[rec:%s] saved A/V recording: %s; video=%d packets/%d bytes "
                "audio=%d packets/%d bytes batches=%d mux_avg=%.2fms mux_max=%.2fms "
                "queue_peak(video=%d,audio=%d)",
                self.run_id, final_path, stats["video_packets"], stats["video_bytes"],
                stats["audio_packets"], stats["audio_bytes"], stats["batches"],
                average_ms, stats["max_batch_seconds"] * 1000,
                stats["max_video_queue"], stats["max_audio_queue"],
            )
        except asyncio.CancelledError:
            for task in get_tasks.values():
                task.cancel()
            if get_tasks:
                await asyncio.gather(*get_tasks.values(), return_exceptions=True)
            try:
                await loop.run_in_executor(executor, muxer.abort)
            finally:
                raise
        except Exception as exc:
            self._recording_error = str(exc)
            self.status_message = f"录像未保存：{exc}"
            logger.warning(
                "[rec:%s] A/V recording incomplete: %s; video=%d packets/%d bytes "
                "audio=%d packets/%d bytes batches=%d mux_total=%.3fs mux_max=%.2fms "
                "queue_peak(video=%d,audio=%d)",
                self.run_id, exc, stats["video_packets"], stats["video_bytes"],
                stats["audio_packets"], stats["audio_bytes"], stats["batches"],
                stats["write_seconds"], stats["max_batch_seconds"] * 1000,
                stats["max_video_queue"], stats["max_audio_queue"], exc_info=True,
            )
            try:
                await loop.run_in_executor(executor, muxer.abort)
            except Exception:
                logger.debug("[rec:%s] muxer abort failed", self.run_id, exc_info=True)
            temp_path.unlink(missing_ok=True)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

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
                self.status_message = "录像未保存：停止录制后等待数据写入超时"
                self._recording_error = self.status_message
                logger.warning("[rec:%s] recorder drain timed out", self.run_id)
            except Exception as exc:
                self._recording_error = str(exc)
                self.status_message = f"录像未保存：收尾失败：{exc}"
                logger.warning("[rec:%s] recorder task stopped with error", self.run_id, exc_info=True)
            self._task = None

        self._video_queue = None
        self._audio_queue = None
        self._started = False
        if self._recording_error:
            logger.warning("[rec:%s] recording not saved: %s", self.run_id, self._recording_error)
