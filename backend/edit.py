"""Source-frame intervals are half-open [in, out)."""

import json, math, uuid
from urllib.parse import quote
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as E

CATEGORIES = ["ore", "stone", "soil", "wood", "foliage", "constructed", "glass", "other"]
TICKS = 254016000000

def number(o, key, default, low, high, integer=False):
    x = float(o.get(key, default))
    if not math.isfinite(x) or not low <= x <= high or (integer and x != int(x)):
        raise ValueError("Invalid " + key)
    return int(x) if integer else x

def select_events(data, options, audio_peaks=None):
    o = dict(options)
    rate = Fraction(data["rate"])
    fps = float(rate)
    frames = int(data["frames"])
    action = o.get("action", "break")
    if action not in ["break", "place", "all"]:
        raise ValueError("Invalid action")
    cats = o.get("categories", CATEGORIES)
    if not isinstance(cats, list) or not cats or any(c not in CATEGORIES for c in cats):
        raise ValueError("Select at least one valid category")
    unit = o.get("unit", "frames")
    if unit not in ["frames", "seconds"]:
        raise ValueError("Invalid padding unit.")
    pre = number(o, "before", 3, 0, 3600 if unit == "frames" else 60, unit == "frames")
    post = number(o, "after", 6, 0, 3600 if unit == "frames" else 60, unit == "frames")
    if unit == "seconds":
        pre, post = int(math.floor(pre * fps + 0.5)), int(math.floor(post * fps + 0.5))
    pre, post = int(pre), int(post)
    if pre + post < 1:
        raise ValueError("Padding must contain at least one source frame")
    every = number(o, "every", 1, 1, 10000, True)
    offset = number(o, "offset", 0, 0, 999999, True)
    gap = number(o, "min_gap", 0, 0, 3600)
    maximum = number(o, "max_clips", 0, 0, 1000000, True)
    max_seconds = number(o, "max_seconds", 0, 0, 86400)
    limit_mode = o.get("limit_mode", "first")
    winner = o.get("duplicate_winner", "confidence")
    if limit_mode not in ["first", "even"]:
        raise ValueError("Invalid clip limit selection")
    if winner not in ["confidence", "first", "last"]:
        raise ValueError("Invalid duplicate preference")
    order = o.get("order", "chronological")
    if order not in ["chronological", "reverse"]:
        raise ValueError("Invalid cut order")
    peak_enabled = o.get("max_audio", False)
    peak_pre = number(o, "peak_before", 3, 0, 3600, True)
    peak_post = number(o, "peak_after", 6, 0, 3600, True)
    floor = number(o, "peak_floor_db", -60, -120, 0)
    channel = o.get("peak_channel", "loudest")
    if channel not in ["loudest", "left", "right"]:
        raise ValueError("Invalid peak channel")
    quiet = o.get("quiet_policy", "keep")
    if quiet not in ["keep", "skip"]:
        raise ValueError("Invalid quiet-window policy")
    if peak_enabled and peak_pre + peak_post < 1:
        raise ValueError("Max audio padding must contain at least one frame")
    start = number(o, "start_frame", 0, 0, frames, True)
    end = number(o, "end_frame", frames, start, frames, True)
    policy = o.get("duplicates", "one")
    if policy not in ["one", "all"]:
        raise ValueError("Invalid duplicate policy")
    events = sorted(
        [
            dict(e)
            for e in data["events"]
            if (action == "all" or e["action"] == action)
            and e["category"] in cats
            and start <= e["frame_index"] < end
        ],
        key=lambda e: e["frame_index"],
    )
    matched = len(events)
    aligned = 0
    quiet_windows = 0
    quiet_removed = 0
    rows = []
    for e in events:
        f = e["frame_index"]
        a = max(start, f - pre)
        b = min(end, f + post)
        if peak_enabled:
            if audio_peaks is None:
                raise ValueError("Max audio requires source audio analysis.")
            import numpy as np

            column = {"loudest": 0, "left": 1, "right": 2}[channel]
            amplitudes = audio_peaks[a:b, column]
            largest = float(np.max(amplitudes)) if len(amplitudes) else 0
            if largest <= 0 or largest < 10 ** (floor / 20):
                quiet_windows += 1
                if quiet == "skip":
                    quiet_removed += 1
                    continue
            else:

                candidates = np.flatnonzero(amplitudes == largest) + a
                center = int(min(candidates, key=lambda x: (abs(int(x) - f), int(x))))
                e.update(peak_frame=center, peak_db=20 * math.log10(largest), search_range=[a, b])
                low, high = (a, b) if o.get("peak_clamp", False) else (start, end)
                a = max(low, center - peak_pre)
                b = min(high, center + peak_post)
                aligned += 1
        if b > a:
            rows.append((e, a, b))
    if policy == "one":
        groups = []
        group_end = -1
        for row in sorted(rows, key=lambda r: r[1]):
            if groups and row[1] < group_end:
                groups[-1].append(row)
                group_end = max(group_end, row[2])
            else:
                groups.append([row])
                group_end = row[2]

        def choose(g):
            if winner == "first":
                return min(g, key=lambda r: r[0]["frame_index"])
            if winner == "last":
                return max(g, key=lambda r: r[0]["frame_index"])
            return max(g, key=lambda r: (r[0]["confidence"], -r[0]["frame_index"]))

        kept = [choose(g) for g in groups]
    else:
        kept = rows
    duplicates_removed = len(rows) - len(kept)
    kept.sort(key=lambda r: (r[1], r[0]["frame_index"]))
    spaced = []
    for row in kept:
        if gap and spaced and row[1] - spaced[-1][2] < gap * fps:
            continue
        spaced.append(row)
    spacing_removed = len(kept) - len(spaced)
    selected = spaced[offset::every]
    if maximum and len(selected) > maximum:
        if limit_mode == "even":
            selected = (
                [selected[round(i * (len(selected) - 1) / (maximum - 1))] for i in range(maximum)]
                if maximum > 1
                else [selected[len(selected) // 2]]
            )
        else:
            selected = selected[:maximum]
    if order == "reverse":
        selected.reverse()
    duration_removed = 0
    if max_seconds:
        budget = int(math.floor(max_seconds * fps + 1e-7))
        bounded = []
        for row in selected:
            size = row[2] - row[1]
            if size <= budget:
                bounded.append(row)
                budget -= size
            else:
                duration_removed += 1
        selected = bounded
    events = [r[0] for r in selected]
    ranges = [(r[1], r[2]) for r in selected]
    return dict(
        events=events,
        ranges=ranges,
        matched=matched,
        duplicates_removed=duplicates_removed,
        clips=len(events),
        output_frames=sum(b - a for a, b in ranges),
        fps=fps,
        before=pre,
        after=post,
        seconds=sum(b - a for a, b in ranges) / fps,
        audio_aligned=aligned,
        quiet_windows=quiet_windows,
        quiet_removed=quiet_removed,
        spacing_removed=spacing_removed,
        duration_removed=duration_removed,
    )


def child(parent, tag, text=None, **attrs):
    node = E.SubElement(parent, tag, attrs)
    if text is not None:
        node.text = str(text)
    return node


def write_xml(data, plan, name, path):
    """Generate linked video + discrete mono tracks for each original audio channel."""

    if not plan["ranges"]:
        raise ValueError("No matching events. Choose other filters or another video.")
    rate = Fraction(data["rate"])
    nominal = round(float(rate))
    ntsc = rate == Fraction(nominal * 1000, 1001)
    if rate.denominator != 1 and not ntsc:
        raise ValueError("Unsupported edit rate: " + str(rate) + ".")
    if TICKS * rate.denominator % rate.numerator:
        raise ValueError("Frame rate cannot be represented exactly.")
    ticks = TICKS * rate.denominator // rate.numerator

    def add_rate(p):
        r = child(p, "rate")
        child(r, "timebase", nominal)
        child(r, "ntsc", "TRUE" if ntsc else "FALSE")

    def characteristics(p):
        sc = child(p, "samplecharacteristics")
        add_rate(sc)
        for k, v in [
            ("width", data["width"]),
            ("height", data["height"]),
            ("anamorphic", "FALSE"),
            ("pixelaspectratio", "square"),
            ("fielddominance", "none"),
        ]:
            child(sc, k, v)

    uid = uuid.uuid4().hex
    root = E.Element("xmeml", version="4")
    seq = child(root, "sequence", id="minecut-" + uid)
    child(seq, "uuid", str(uuid.uuid4()))
    child(seq, "duration", plan["output_frames"])
    add_rate(seq)
    child(seq, "name", name)
    seq.set("MZ.WorkOutPoint", str(plan["output_frames"] * ticks))
    seq.set("MZ.EditLine", "0")
    seq.set("explodedTracks", "true")
    media = child(seq, "media")
    video = child(media, "video")
    characteristics(child(video, "format"))
    tracks = [child(video, "track")]
    channels = int(data.get("channels", 0))
    if channels > 2:
        raise ValueError("Use mono/stereo source audio. Multichannel layouts are not yet supported.")
    if channels:
        audio = child(media, "audio")
        child(audio, "numOutputChannels", 2)
        fmt = child(child(audio, "format"), "samplecharacteristics")
        child(fmt, "depth", 16)
        child(fmt, "samplerate", data["sample_rate"])
        for c in range(channels):
            tracks.append(
                child(
                    audio,
                    "track",
                    currentExplodedTrackIndex=str(c),
                    totalExplodedTrackCount=str(channels),
                )
            )
    source = Path(data["edit_source"]).resolve()
    file_id = "file-" + uid
    pos = 0
    for i, (a, b) in enumerate(plan["ranges"]):
        ids = ["mc-" + uid + "-" + str(t) + "-" + str(i) for t in range(len(tracks))]
        for t, track in enumerate(tracks):
            clip = child(track, "clipitem", id=ids[t])
            child(clip, "masterclipid", "master-" + uid)
            child(clip, "name", source.name)
            child(clip, "enabled", "TRUE")
            child(clip, "duration", data["frames"])
            add_rate(clip)
            for k, v in [
                ("start", pos),
                ("end", pos + b - a),
                ("in", a),
                ("out", b),
                ("pproTicksIn", a * ticks),
                ("pproTicksOut", b * ticks),
            ]:
                child(clip, k, v)
            file = child(clip, "file", id=file_id)
            if i == 0 and t == 0:

                uri = (
                    "file://localhost/" + quote(source.as_posix(), safe="/")
                    if source.drive
                    else source.as_uri()
                )
                child(file, "name", source.name)
                child(file, "pathurl", uri)
                add_rate(file)
                child(file, "duration", data["frames"])
                tc = child(file, "timecode")
                add_rate(tc)
                child(tc, "string", "00:00:00:00")
                child(tc, "frame", 0)
                child(tc, "displayformat", "NDF")
                fm = child(file, "media")
                characteristics(child(fm, "video"))
                if channels:
                    au = child(fm, "audio")
                    sc = child(au, "samplecharacteristics")
                    child(sc, "depth", 16)
                    child(sc, "samplerate", data["sample_rate"])
                    child(au, "channelcount", channels)
            if t:
                st = child(clip, "sourcetrack")
                child(st, "mediatype", "audio")
                child(st, "trackindex", t)
            for j, ref in enumerate(ids):
                link = child(clip, "link")
                child(link, "linkclipref", ref)
                child(link, "mediatype", "video" if j == 0 else "audio")
                child(link, "trackindex", max(1, j))
                child(link, "clipindex", i + 1)
                if j:
                    child(link, "groupindex", 1)
        pos += b - a
    E.indent(root, space="  ")
    E.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path