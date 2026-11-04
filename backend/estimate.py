"""Whole-job estimates from observed stage throughput and local run history."""

import json, math, time
from pathlib import Path

class Estimate:
    phases = ("video", "audio", "infer", "peaks", "convert", "finish")

    def __init__(
        self,
        emit,
        work,
        meta,
        device,
        batch,
        source_range=None,
        cached=False,
        prepared=False,
        peaks=False,
        convert=False,
        clock=time.monotonic,
    ):
        self.output = emit
        self.clock = clock
        self.path = Path(work) / "performance.json"
        try:
            self.history = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.history = {}
        self.history = (
            {
                k: v
                for k, v in self.history.items()
                if isinstance(v, (int, float)) and math.isfinite(v) and v > 0
            }
            if isinstance(self.history, dict)
            else {}
        )
        duration = meta["duration"]
        fps = meta["fps"]
        frames = meta["frames"] or round(duration * fps)
        if source_range:
            first = round(source_range["start_seconds"] * fps)
            last = round(source_range["end_seconds"] * fps)
            frames = min(frames, (math.ceil(last / 48) + 2) * 48) - max(0, (first // 48 - 2) * 48)
        self.units = dict(
            video=frames / fps,
            audio=duration,
            infer=frames,
            peaks=duration,
            convert=duration,
            finish=1,
        )
        cpu = device == "cpu"
        defaults = dict(
            video=1 / (3 if cpu else 6),
            audio=1 / 200,
            infer=1 / (100 if cpu else 1400),
            peaks=1 / 90,
            convert=1,
            finish=1,
        )
        self.keys = {p: p + ":" + device for p in self.phases}
        self.keys["video"] += (
            ":"
            + str(meta.get("codec"))
            + ":"
            + str(meta.get("width"))
            + "x"
            + str(meta.get("height"))
        )
        self.keys["convert"] = self.keys["video"] + ":convert"
        self.keys["infer"] += ":" + str(batch)
        self.remaining = {
            p: max(0.1, self.units[p] * self.history.get(self.keys[p], defaults[p]))
            for p in self.phases
        }
        if cached:
            for p in ("video", "audio", "infer"):
                self.remaining[p] = 0
        elif prepared:
            for p in ("video", "audio"):
                self.remaining[p] = 0
        if not peaks:
            self.remaining["peaks"] = 0
        if not convert:
            self.remaining["convert"] = 0
        self.current = None
        self.started = clock()
        self.samples = {}

    def begin(self, phase):
        if phase == self.current:
            return
        now = self.clock()
        if self.current is not None:
            elapsed = now - self.started
            p = self.current
            if elapsed >= 1 and self.units[p] > 0:
                self.samples[self.keys[p]] = elapsed / self.units[p]
        for p in self.phases[: self.phases.index(phase)]:
            self.remaining[p] = 0
        self.current = phase
        self.started = now

    def __call__(self, kind, **data):
        if kind == "progress":
            stage = data.get("stage", "")
            phase = None
            if stage.startswith("Preparing video"):
                phase = "video"
            elif stage == "Preparing audio":
                phase = "audio"
            elif stage == "Loading model" or stage.startswith("Analyzing on"):
                phase = "infer"
            elif stage == "Finding audio peaks":
                phase = "peaks"
            elif stage == "Creating Premiere-compatible media":
                phase = "convert"
            elif stage == "Building cut":
                phase = "finish"
            if phase:
                self.begin(phase)
            remaining = dict(self.remaining)
            if self.current:
                measured = data.get("eta_seconds")
                if measured is not None and math.isfinite(measured) and measured >= 0:
                    remaining[self.current] = measured
                else:
                    remaining[self.current] = max(
                        1, remaining[self.current] - (self.clock() - self.started)
                    )
            data["eta_total_seconds"] = sum(remaining.values())
        self.output(kind, **data)

    def save(self):
        self.begin("finish")
        for key, value in self.samples.items():
            old = self.history.get(key, value)
            self.history[key] = 0.35 * old + 0.65 * value
        try:
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.history))
            temp.replace(self.path)
        except OSError:
            pass