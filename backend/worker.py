"""JSON-line progress worker with no network listener."""

import argparse, json, os, shutil, sys, time, traceback, uuid
from pathlib import Path

def emit(kind, **data):
    for key in ("stage", "message"):
        if isinstance(data.get(key), str):
            text = data[key].rstrip()
            data[key] = text + "." if text and text[-1] not in ".!?..." else text
    print(json.dumps(dict(type=kind, **data)), flush=True)

def main():
    emit = globals()["emit"]
    estimator = None
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    a = parser.parse_args()
    root = Path(a.root).resolve()
    sys.path.insert(0, str(root / "model" / "code"))
    request = json.loads(sys.stdin.readline())
    cmd = request.get("command", "analyze")
    import engine
    from edit import select_events, write_xml, number
    from audio_peaks import inventory, frame_peaks
    from estimate import Estimate

    model = root / "model" / "model.pt"
    ffmpeg = root / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not ffmpeg.exists():
        ffmpeg = shutil.which("ffmpeg")
    default = (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "Library" / "Caches"))) / "Minecut"
    )
    work = Path(request.get("work") or default).resolve()
    if cmd == "media_info":
        source = Path(request["source"]).resolve()
        key = engine.identity(source, engine.MODEL_SHA, request.get("source_range"))
        cached_events = work / "analysis" / key / "events.json"
        valid = False
        if cached_events.exists():
            try:
                cached_data = json.loads(cached_events.read_text())
                valid = cached_data.get("identity") == key and isinstance(
                    cached_data.get("events"), list
                )
            except (OSError, ValueError):
                pass
        emit(
            "result",
            ok=True,
            audio_tracks=inventory(source),
            events_path=str(cached_events) if valid else None,
        )
        return
    if cmd == "diagnose":
        import torch

        emit(
            "result",
            ok=True,
            model_exists=model.exists(),
            ffmpeg=bool(ffmpeg),
            cuda=torch.cuda.is_available(),
            gpu=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            torch=torch.__version__,
            python=sys.version.split()[0],
        )
        return
    default = (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "Library" / "Caches"))) / "Minecut"
    )
    work = Path(request.get("work") or default).resolve()
    work.mkdir(parents=True, exist_ok=True)

    lock = (work / ".worker.lock").open("a+b")
    lock.seek(0)
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise ValueError(
            "Another Minecut job is using this work folder. Finish or cancel it first."
        )

    def setup_estimate(meta, cached=False, prepared=False):
        compatible = (
            meta.get("codec") in ["av1", "vp9", "vp8"]
            and not (events.parent / "premiere-source.mp4").exists()
        )
        return Estimate(
            emit,
            work,
            meta,
            request.get("device", "auto"),
            int(request.get("batch", 8)),
            request.get("source_range"),
            cached,
            prepared,
            request.get("options", {}).get("max_audio", False),
            compatible,
        )

    def ensure_compatible(data, source, events, workdir):
        if data.get("codec") not in ["av1", "vp9", "vp8"]:
            return data
        if not ffmpeg:
            raise ValueError("FFmpeg not found for Premiere-compatible conversion")
        compatible = events.parent / "premiere-source.mp4"
        if not compatible.exists():
            if shutil.disk_usage(workdir).free < max(2 * 1024**3, data["duration"] * 2500000):
                raise ValueError("Insufficient space for a Premiere-compatible source copy")
            tmp = events.parent / "premiere-source.part.mp4"
            emit("progress", stage="Creating Premiere-compatible media", fraction=0.99)
            engine.run_ffmpeg(
                ffmpeg,
                [
                    "-threads",
                    "4",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-fps_mode",
                    "passthrough",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    str(tmp),
                ],
                events.parent / "compatibility.log",
                lambda kind, **p: emit(
                    kind, **dict(p, stage="Creating Premiere-compatible media", fraction=0.99)
                ),
                data["duration"],
            )
            check = engine.probe(tmp)
            if check["frames"] != data["frames"] or check["rate"] != data["rate"]:
                raise ValueError("Compatibility copy timing mismatch")
            tmp.replace(compatible)
        data = dict(data, edit_source=str(compatible))
        return data

    if cmd == "build":
        events = Path(request["events_path"]).resolve()

        data = json.loads(events.read_text(encoding="utf8"))
        source = Path(data["video"])
        if not source.exists():
            raise ValueError("Source media is missing")
        estimator = setup_estimate(data, cached=True)
        emit = estimator
    elif cmd == "analyze":
        source = Path(request["source"]).resolve()
        if not source.is_file():
            raise ValueError("Select a valid source video")
        if not model.is_file():
            raise ValueError("Bundled model.pt is missing. Reinstall the complete package.")
        if not ffmpeg:
            raise ValueError("FFmpeg not found. Reinstall the full package or put FFmpeg on PATH.")
        emit("progress", stage="Checking model and media", fraction=0)
        model_hash = engine.sha(model)
        if model_hash != engine.MODEL_SHA:
            raise ValueError("Model integrity check failed; expected the bundled v3 checkpoint.")
        batch = int(request.get("batch", 8))
        if not 1 <= batch <= 128:
            raise ValueError("Batch size must be between 1 and 128")
        key = engine.identity(source, model_hash, request.get("source_range"))
        folder = work / "analysis" / key
        events = folder / "events.json"
        metadata = engine.probe(source)
        estimator = setup_estimate(
            metadata, cached=events.exists(), prepared=(folder / "ready.json").exists()
        )
        emit = estimator
        if events.exists():
            data = json.loads(events.read_text(encoding="utf8"))
            emit("progress", stage="Using cached analysis", fraction=0.4)
        else:
            prep_started = time.monotonic()
            emit("progress", stage="Preparing video", fraction=0)
            engine.prepare(
                source,
                folder,
                ffmpeg,
                emit,
                request.get("source_range"),
                hardware=request.get("device", "auto") != "cpu",
            )
            prep_seconds = time.monotonic() - prep_started
            emit("progress", stage="Loading model", fraction=0.4)
            data = engine.infer(folder, model, request.get("device", "auto"), batch, emit)
            data["preparation_seconds"] = prep_seconds
            data["edit_source"] = str(source)
            data["identity"] = key
            data["source_size"] = source.stat().st_size
            data["source_mtime_ns"] = source.stat().st_mtime_ns
            engine.atomic(events, data)
    else:
        raise ValueError("Unknown worker command")
    if source.stat().st_size != data.get(
        "source_size", source.stat().st_size
    ) or source.stat().st_mtime_ns != data.get("source_mtime_ns", source.stat().st_mtime_ns):
        raise ValueError("Source changed since analysis. Analyze it again.")
    options = dict(request.get("options", {}))
    if "selection_start" in data:
        options.update(start_frame=data["selection_start"], end_frame=data["selection_end"])

    preliminary = select_events(data, dict(options, max_audio=False))
    peaks = None
    if options.get("max_audio", False):
        if (
            number(options, "peak_before", 3, 0, 3600, True)
            + number(options, "peak_after", 6, 0, 3600, True)
            < 1
        ):
            raise ValueError("Max audio padding must contain at least one frame")
        track = number(options, "peak_stream", 0, 0, 255, True)
        if preliminary["matched"]:
            peaks, channels = frame_peaks(source, data, track, work / "audio-peaks", emit)
            if options.get("peak_channel") == "right" and channels == 1:
                raise ValueError(
                    "The peak-search track is mono. Choose Loudest channel or Left / mono."
                )
    emit("progress", stage="Planning cut", fraction=0.98)
    plan = select_events(data, options, peaks)
    summary = {k: v for k, v in plan.items() if k not in ["events", "ranges"]}
    if not plan["clips"]:
        if estimator:
            estimator.save()
        emit(
            "result",
            ok=True,
            events_path=str(events),
            xml_path=None,
            summary=summary,
            source=data["video"],
            message="No matching events. Adjust filters and rebuild without analyzing again.",
        )
        return

    data = ensure_compatible(data, source, events, work)
    emit("progress", stage="Building cut", fraction=0.99)
    job = work / "edits" / uuid.uuid4().hex
    job.mkdir(parents=True)
    name = (
        str(options.get("name") or "Minecut — " + options.get("action", "break").title())[:120]
        + " — "
        + time.strftime("%H%M%S")
    )
    xml = job / "Minecut.xml"
    write_xml(data, plan, name, xml)
    engine.atomic(
        job / "cut-plan.json",
        dict(options=options, source=data["edit_source"], rate=data["rate"], name=name, **plan),
    )
    if estimator:
        estimator.save()
    emit(
        "result",
        ok=True,
        events_path=str(events),
        xml_path=str(xml),
        sequence_name=name,
        source=data["edit_source"],
        summary=summary,
        device=data.get("device"),
        rate=data["rate"],
    )

if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    try:
        main()
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        emit("error", message=str(e))
        sys.exit(1)