"""Stream source audio once into a small, timestamp-aligned per-video-frame peak cache."""

import hashlib, json, time
from pathlib import Path
import av
import numpy as np


def inventory(source):
    with av.open(str(source)) as container:
        return [
            dict(
                index=i,
                channels=s.codec_context.channels,
                name=s.metadata.get("title") or s.metadata.get("handler_name") or "Audio",
                language=s.metadata.get("language", ""),
            )
            for i, s in enumerate(container.streams.audio)
        ]


def frame_peaks(source, data, stream_index, cache, emit):
    source = Path(source)
    stat = source.stat()
    n = int(data["frames"])
    fps = float(data["fps"])
    key = hashlib.sha256(
        json.dumps(
            [
                str(source.resolve()),
                stat.st_size,
                stat.st_mtime_ns,
                stream_index,
                n,
                data["rate"],
                data.get("source_start", 0),
                "peaks-v1",
            ]
        ).encode()
    ).hexdigest()[:32]
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (key + ".npz")
    if target.exists():
        with np.load(target, allow_pickle=False) as z:
            peaks = z["peaks"]
            channels = int(z["channels"])
        if peaks.shape == (n, 3) and np.isfinite(peaks).all():
            return peaks, channels
    peaks = np.zeros((n, 3), np.float32)
    started = time.monotonic()
    last = 0
    with av.open(str(source)) as container:
        if not container.streams.audio:
            return peaks, 0
        if stream_index >= len(container.streams.audio):
            raise ValueError("Selected peak-search audio track is unavailable")
        stream = container.streams.audio[stream_index]
        channels = stream.codec_context.channels
        resampler = av.AudioResampler(
            format="fltp", layout=stream.codec_context.layout.name, rate=stream.rate
        )
        for decoded in container.decode(stream):
            for frame in resampler.resample(decoded):
                if frame.pts is None:
                    raise ValueError("Audio timestamps are missing; cannot align Max audio safely")
                audio = frame.to_ndarray()
                if not np.isfinite(audio).all():
                    raise ValueError("Audio contains invalid samples")
                amplitude = np.abs(audio)
                values = np.stack(
                    [amplitude.max(axis=0), amplitude[0], amplitude[min(1, channels - 1)]], axis=1
                )
                seconds = float(frame.pts * frame.time_base) - float(data.get("source_start", 0))
                indices = np.floor(
                    (seconds + np.arange(frame.samples) / frame.sample_rate) * fps + 1e-7
                ).astype(np.int64)
                valid = (indices >= 0) & (indices < n)
                indices = indices[valid]
                values = values[valid]
                if len(indices):
                    starts = np.r_[0, np.flatnonzero(np.diff(indices)) + 1]
                    bins = indices[starts]
                    peaks[bins] = np.maximum(
                        peaks[bins], np.maximum.reduceat(values, starts, axis=0)
                    )
                now = time.monotonic()
                if now - last >= 0.5:
                    fraction = max(
                        0,
                        min(
                            0.999,
                            (seconds + frame.samples / frame.sample_rate)
                            / max(data["duration"], 0.001),
                        ),
                    )
                    emit(
                        "progress",
                        stage="Finding audio peaks",
                        fraction=0.98,
                        eta_seconds=(
                            (now - started) * (1 - fraction) / fraction if fraction > 0.01 else None
                        ),
                    )
                    last = now

    tmp = target.with_suffix(".tmp")
    with tmp.open("wb") as f:
        np.savez(f, peaks=peaks, channels=np.array(channels))
    tmp.replace(target)
    return peaks, channels
