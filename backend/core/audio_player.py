"""Server-side audio playback for execution-plan audio steps."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional


_PLAYBACK_LOCK = asyncio.Lock()


def list_output_devices() -> list[dict]:
    try:
        import sounddevice as sd
        devices = sd.query_devices()
    except Exception as exc:
        raise RuntimeError(f"音频设备枚举失败：{exc}") from exc

    output = []
    for index, device in enumerate(devices):
        channels = int(device.get("max_output_channels", 0) or 0)
        if channels <= 0:
            continue
        output.append({
            "index": index,
            "name": str(device.get("name") or f"Output {index}"),
            "channels": channels,
            "default_sample_rate": float(device.get("default_samplerate") or 0),
        })
    return output


def _play_blocking(path: str, device_index: Optional[int], volume_percent: int) -> dict:
    import sounddevice as sd
    import soundfile as sf

    started_at = datetime.now().astimezone().isoformat()
    try:
        data, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    except Exception as exc:
        raise RuntimeError(f"音频解码失败：{exc}") from exc
    if data.size == 0 or sample_rate <= 0:
        raise RuntimeError("音频文件没有可播放的音频数据")

    data *= max(0, min(100, int(volume_percent))) / 100.0
    try:
        sd.play(data, samplerate=int(sample_rate), device=device_index, blocking=True)
    except Exception as exc:
        raise RuntimeError(f"音频播放失败，请检查扬声器和播放设备：{exc}") from exc

    finished_at = datetime.now().astimezone().isoformat()
    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(len(data) / float(sample_rate), 3),
        "sample_rate": int(sample_rate),
        "frames": int(len(data)),
    }


async def play_audio_file(
    path: str | Path,
    device_index: Optional[int] = None,
    volume_percent: int = 80,
) -> dict:
    file_path = Path(path)
    if not file_path.is_file():
        raise RuntimeError("音频语料文件不存在")
    if device_index is not None:
        try:
            device_index = int(device_index)
            import sounddevice as sd
            info = sd.query_devices(device_index)
            if int(info.get("max_output_channels", 0) or 0) <= 0:
                raise RuntimeError("所选设备不支持音频输出")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"所选音频输出设备不可用：{exc}") from exc

    async with _PLAYBACK_LOCK:
        try:
            return await asyncio.to_thread(
                _play_blocking, str(file_path), device_index, volume_percent
            )
        except asyncio.CancelledError:
            try:
                import sounddevice as sd
                await asyncio.to_thread(sd.stop)
            except Exception:
                pass
            raise
