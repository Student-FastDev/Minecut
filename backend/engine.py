"""Local model runtime; same weights, spatial preprocessing and CTC threshold as v3."""

import contextlib, hashlib, json, os, shutil, subprocess, time, math
from fractions import Fraction
from pathlib import Path
import av
import numpy as np

VERSION = "minecut-v104-range-cfr-copyts-bf16"
MODEL_SHA = "7f9ad0b4290fa99c343f030903a4ede572a90ecff35de72b2bb5361a3cfb3106"


@contextlib.contextmanager
def prefetched(iterable):
    """One queued CPU batch; overlap decoding with GPU work without worker copies."""
    import queue, threading

    pending = queue.Queue(maxsize=1)
    stop = threading.Event()

    def put(value):
        while not stop.is_set():
            try:
                pending.put(value, timeout=0.1)
                return
            except queue.Full:
                pass

    def produce():
        try:
            for value in iterable:
                if stop.is_set():
                    break
                put((True, value))
        except BaseException as e:
            put((False, e))
        finally:
            put((True, None))

    thread = threading.Thread(target=produce, daemon=True)
    thread.start()

    def consume():
        while True:
            ok, value = pending.get()
            if not ok:
                raise value
            if value is None:
                return
            yield value

    try:
        yield consume()
    finally:
        stop.set()
        thread.join(timeout=5)


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf8")
    tmp.replace(path)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def identity(source, model, source_range=None):
    p = Path(source).resolve()
    st = p.stat()
    h = hashlib.sha256()
    h.update(json.dumps([str(p), st.st_size, st.st_mtime_ns, model, VERSION]).encode())
    if source_range is not None:
        h.update(json.dumps(source_range, sort_keys=True).encode())
    with p.open("rb") as f:
        h.update(f.read(1024 * 1024))
        f.seek(max(0, st.st_size - 1024 * 1024))
        h.update(f.read())
    return h.hexdigest()[:32]


def probe(path):
    with av.open(str(path)) as c:
        if not c.streams.video:
            raise ValueError("The source has no video stream")
        v = c.streams.video[0]
        r = Fraction(v.average_rate or v.base_rate)
        if not 1 <= float(r) <= 240:
            raise ValueError("Unsupported source frame rate")
        nominal = round(float(r))
        if r.denominator != 1 and r != Fraction(nominal * 1000, 1001):
            raise ValueError(
                "Unsupported or variable frame rate. Convert to constant frame rate first."
            )
        a = c.streams.audio[0] if c.streams.audio else None
        channels = a.codec_context.channels if a else 0
        if channels > 2:
            raise ValueError("Minecut currently supports mono or stereo audio")
        return dict(
            video=str(Path(path).resolve()),
            rate=str(r),
            fps=float(r),
            frames=int(v.frames or 0),
            duration=(
                float(v.duration * v.time_base) if v.duration else float(c.duration or 0) / 1e6
            ),
            width=v.width,
            height=v.height,
            codec=v.codec_context.name,
            channels=channels,
            sample_rate=a.rate if a else 48000,
            audio_start=float((a.start_time or 0) * a.time_base) if a else 0,
            source_start=float((v.start_time or 0) * v.time_base),
        )


def run_ffmpeg(exe, args, log, emit, duration, progress_start=0):

    with Path(log).open("w", encoding="utf8") as err:
        p = subprocess.Popen(
            [
                str(exe),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-progress",
                "pipe:1",
                "-nostats",
            ]
            + args,
            stdout=subprocess.PIPE,
            stderr=err,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            last = 0
            started = time.monotonic()
            for line in p.stdout:
                if line.startswith("out_time_us=") and time.monotonic() - last > 1:
                    try:
                        progress = max(
                            0,
                            min(
                                0.99,
                                (float(line.split("=")[1]) / 1e6 - progress_start)
                                / max(duration, 0.001),
                            ),
                        )
                    except ValueError:
                        continue
                    elapsed = time.monotonic() - started
                    eta = (
                        elapsed * (1 - progress) / progress
                        if progress > 0.01 and elapsed >= 2
                        else None
                    )
                    emit(
                        "progress",
                        stage="Preparing video",
                        fraction=progress * 0.35,
                        eta_seconds=eta,
                    )
                    last = time.monotonic()
            if p.wait():
                raise RuntimeError(
                    "Media preparation failed: " + Path(log).read_text(encoding="utf8")[-1800:]
                )
        finally:
            if p.poll() is None:
                p.terminate()
                p.wait()
            p.stdout.close()


def mel(audio):
    from scipy.signal import stft

    audio = np.pad(audio, (200, 200))
    _, _, z = stft(
        audio, fs=16000, nperseg=400, noverlap=240, nfft=512, boundary=None, padded=False
    )
    hz = 700 * (10 ** (np.linspace(0, 2595 * np.log10(1 + 8000 / 700), 66) / 2595) - 1)
    f = np.arange(257) * 16000 / 512
    bank = np.maximum(
        0,
        np.minimum(
            (f[None, :] - hz[:-2, None]) / (hz[1:-1, None] - hz[:-2, None]),
            (hz[2:, None] - f[None, :]) / (hz[2:, None] - hz[1:-1, None]),
        ),
    )
    return np.log(np.maximum(bank @ np.abs(z) ** 2, 1e-10)).T.astype(np.float16)


def prepare(source, folder, ffmpeg, emit, source_range=None, hardware=True):
    folder.mkdir(parents=True, exist_ok=True)
    ready = folder / "ready.json"
    if ready.exists() and all((folder / x).exists() for x in ["views.mp4", "timing_audio.npz"]):
        return json.loads(ready.read_text())
    row = probe(source)
    seek = []
    trim = ""
    offset = 0
    expected = None
    if source_range is not None:
        rate = Fraction(row["rate"])
        begin = float(source_range["start_seconds"])
        end = float(source_range["end_seconds"])
        if (
            not math.isfinite(begin)
            or not math.isfinite(end)
            or begin < 0
            or end <= begin
            or end > row["duration"] + 1 / float(rate)
        ):
            raise ValueError("Invalid selected source range")
        first = int(math.floor(begin * float(rate) + 0.5))
        last = int(math.floor(end * float(rate) + 0.5))

        offset = max(0, (first // 48 - 2) * 48)
        limit = (math.ceil(last / 48) + 2) * 48
        total = row["frames"] or int(round(row["duration"] * float(rate)))
        limit = min(total, limit)
        expected = limit - offset
        row.update(
            frames=total,
            selection_start=first,
            selection_end=min(last, total),
            analysis_offset=offset,
        )
        lo = row["source_start"] + offset / float(rate)
        hi = row["source_start"] + limit / float(rate)
        seek_start = max(0, offset / float(rate) - 2)
        seek = [
            "-ss",
            format(seek_start, ".6f"),
            "-t",
            format(limit / float(rate) - seek_start, ".6f"),
        ]

        trim = f"[0:v:0]trim=start={max(row['source_start'],lo-0.000001):.6f}:end={hi-0.000001:.6f}[selected];"
    if shutil.disk_usage(folder).free < max(2 * 1024**3, row["duration"] * 120000):
        raise ValueError(
            "Not enough free space for analysis cache. Free space or choose another cache folder."
        )
    proxy = folder / "views.part.mp4"
    filt = "[0:v:0]split=2[g][c];[g]scale=224:128:flags=bilinear,pad=224:160:0:0[g1];[c]crop=trunc(ih/4)*2:trunc(ih/4)*2,scale=160:160:flags=bilinear[c1];[g1][c1]hstack=inputs=2[v]"
    if trim:
        filt = trim + filt.replace("[0:v:0]split", "[selected]split")

    decode_threads = min(8, os.cpu_count() or 4)
    encode_threads = 2
    args = (
        ["-threads", str(decode_threads), "-copyts"]
        + seek
        + [
            "-i",
            str(source),
            "-filter_complex_threads",
            "2",
            "-filter_complex",
            filt,
            "-map",
            "[v]",
            "-an",
            "-fps_mode",
            "passthrough",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-g",
            "32",
            "-threads",
            str(encode_threads),
            "-pix_fmt",
            "yuv420p",
            "-avoid_negative_ts",
            "disabled",
            "-movie_timescale",
            "60000",
            str(proxy),
        ]
    )
    row["decode_device"] = "cpu"
    progress_duration = expected / row["fps"] if expected else row["duration"]
    progress_start = row["source_start"] + offset / row["fps"]
    if hardware:
        try:
            run_ffmpeg(
                ffmpeg,
                ["-hwaccel", "cuda"] + args,
                folder / "hardware-prepare.log",
                emit,
                progress_duration,
                progress_start,
            )
            row["decode_device"] = "cuda"
        except RuntimeError:
            emit("progress", stage="Preparing video on CPU", fraction=0)
            run_ffmpeg(
                ffmpeg, args, folder / "prepare.log", emit, progress_duration, progress_start
            )
    else:
        run_ffmpeg(ffmpeg, args, folder / "prepare.log", emit, progress_duration, progress_start)
    with av.open(str(proxy)) as c:
        stream = c.streams.video[0]
        stream.thread_count = 2
        pts = np.array([float(f.pts * f.time_base) for f in c.decode(stream)], np.float64)
    if not len(pts) or ((expected or row["frames"]) and len(pts) != (expected or row["frames"])):
        raise ValueError("Decoded source/proxy frame count mismatch")
    fps = row["fps"]
    if (
        np.any(np.diff(pts) <= 0)
        or np.max(np.abs((pts - pts[0]) * fps - np.arange(len(pts)))) > 0.025
    ):
        raise ValueError(
            "Variable-rate or discontinuous timestamps. Convert the source to constant frame rate before analyzing."
        )
    if source_range is None:
        row["frames"] = len(pts)
        row["source_start"] = float(pts[0])
    elif abs((pts[0] - row["source_start"]) * fps - offset) > 0.025:
        raise ValueError("Selected source frame offset mismatch")
    row["analysis_frames"] = len(pts)
    row["has_audio"] = bool(row["channels"])
    emit("progress", stage="Preparing audio", fraction=0.36)
    raw = folder / "audio.f32"
    if row["channels"]:
        run_ffmpeg(
            ffmpeg,
            [
                "-threads",
                "2",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "f32le",
                str(raw),
            ],
            folder / "audio.log",
            lambda *a, **k: None,
            row["duration"],
        )
        if not raw.stat().st_size:
            raise ValueError("Audio stream is empty or unreadable")
        sound = np.memmap(raw, dtype="<f4", mode="r")
        pieces = []
        hop = 480000
        for start in range(0, len(sound), hop):
            lo = max(0, start - 320)
            hi = min(len(sound), start + hop + 320)
            m = mel(np.asarray(sound[lo:hi]))
            offset = (start - lo) // 160
            count = min(3000, (len(sound) - start + 159) // 160)
            pieces.append(m[offset : offset + count])
        audio = np.concatenate(pieces)
        del sound
        raw.unlink()
    else:
        audio = np.full((int(len(pts) / fps * 100) + 1, 64), -23.02585, np.float16)
        row["audio_start"] = float(pts[0])
    np.savez(
        folder / "timing_audio.npz",
        pts=pts,
        mel=audio,
        audio_start=np.array(row["audio_start"]),
        has_audio=np.array(row["has_audio"]),
    )
    proxy.replace(folder / "views.mp4")
    atomic(ready, row)
    return row


def infer(folder, model_path, device_pref, batch, emit):
    import torch
    from network import Spotter, decode_sequence
    from stream_data import StreamWindows
    from torch.utils.data import DataLoader

    started = time.monotonic()
    torch.set_num_threads(min(6, os.cpu_count() or 2))
    device = (
        "cuda"
        if device_pref == "auto" and torch.cuda.is_available()
        else ("cpu" if device_pref == "auto" else device_pref)
    )
    if device not in ["cuda", "cpu"]:
        raise ValueError("Choose Auto, NVIDIA GPU or CPU")
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError(
            "CUDA is unavailable. Choose Auto or CPU; check your NVIDIA driver for GPU acceleration."
        )
    dtype = torch.bfloat16 if device == "cuda" and torch.cuda.is_bf16_supported() else torch.float16

    torch.backends.cudnn.benchmark = False
    ck = torch.load(model_path, map_location="cpu", weights_only=False)
    if "threshold" not in ck:
        raise ValueError("Checkpoint has no calibrated threshold")
    threshold = ck["threshold"]
    categories = ck["config"]["categories"]
    model = Spotter(pretrained=False).to(device).eval()
    model.load_state_dict(ck["model"])
    del ck
    row = json.loads((folder / "ready.json").read_text())
    n = row.get("analysis_frames", row["frames"])
    offset = row.get("analysis_offset", 0)
    logits = np.full((n, 2), -40, np.float16)
    cats = np.zeros((n, 2, 8), np.float16)
    loader = DataLoader(
        StreamWindows(folder),
        batch_size=min(batch, 2 if device == "cpu" else batch),
        num_workers=0,
        pin_memory=device == "cuda",
    )
    chunk = batch
    last = 0
    with torch.inference_mode(), prefetched(loader) as batches:
        for b in batches:
            size = len(b["start"])
            j = 0
            while j < size:
                count = min(chunk, size - j)
                part = {}
                try:
                    part = {
                        k: v[j : j + count].to(device, non_blocking=device == "cuda")
                        for k, v in b.items()
                        if k != "start"
                    }
                    with (
                        torch.autocast("cuda", dtype=dtype)
                        if device == "cuda"
                        else contextlib.nullcontext()
                    ):
                        out = model(
                            part["images"],
                            part["audio"],
                            part["audio_mask"],
                            part["fps"],
                            auxiliary=False,
                        )
                    a = out["tokens"].float().cpu().numpy()
                    c = out["categories"].float().cpu().numpy()
                except torch.cuda.OutOfMemoryError:
                    del part
                    if count == 1:
                        raise RuntimeError(
                            "GPU memory exhausted even at batch 1. Close GPU-heavy apps or select CPU."
                        )
                    chunk = max(1, count // 2)
                    torch.cuda.empty_cache()
                    emit("progress", stage="Reducing GPU batch to " + str(chunk), fraction=0.4)
                    continue
                for k, start in enumerate(b["start"][j : j + count]):
                    local = np.arange(24, 72)
                    absolute = int(start) + local
                    ok = (absolute >= 0) & (absolute < n)
                    logits[absolute[ok]] = a[k, local[ok]]
                    cats[absolute[ok]] = c[k, local[ok]]
                j += count
                del out, part
            if time.monotonic() - last > 1:
                done = min(n, int(b["start"][-1]) + 72)
                emit(
                    "progress",
                    stage="Analyzing on " + device.upper(),
                    fraction=0.4 + 0.58 * done / n,
                    frames=done,
                    total_frames=n,
                    eta_seconds=(time.monotonic() - started) * (n - done) / max(done, 1),
                )
                last = time.monotonic()
    with np.load(folder / "timing_audio.npz") as z:
        pts = z["pts"]
    events = [
        dict(
            frame_index=f + offset,
            timestamp_seconds=float(pts[f] - row["source_start"]),
            action=["break", "place"][act],
            category=categories[cat],
            confidence=score,
        )
        for f, act, cat, score in decode_sequence(logits, cats, threshold)
    ]
    return dict(
        row,
        events=events,
        threshold=threshold,
        device=device,
        inference_seconds=time.monotonic() - started,
        model_sha256=MODEL_SHA,
    )
