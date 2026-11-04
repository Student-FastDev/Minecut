import collections, json, pathlib
import av, numpy as np, torch
from torch.utils.data import IterableDataset

class StreamWindows(IterableDataset):
    def __init__(self, folder, length=96):
        self.folder = pathlib.Path(folder)
        self.length = length

    def __iter__(self):
        with np.load(self.folder / "timing_audio.npz") as z:
            meta = {k: z[k] for k in z.files}
        row = json.loads((self.folder / "ready.json").read_text())
        n = len(meta["pts"])
        length = self.length
        with av.open(str(self.folder / "views.mp4")) as container:
            stream = container.streams.video[0]
            stream.thread_count = 2
            frames = iter(container.decode(stream))
            buffer = collections.deque()
            base = 0
            next_index = 0
            for start in range(-length // 4, n - length // 4, length // 2):
                first = max(0, start)
                last = min(n, start + length)
                while base < first:
                    buffer.popleft()
                    base += 1
                while next_index < last:
                    frame = next(frames, None)
                    if frame is None:
                        raise RuntimeError("Proxy ended before source frame count")
                    buffer.append(frame.to_ndarray(format="rgb24"))
                    next_index += 1
                visual = np.stack(buffer)
                if start < 0 or start + length > n:
                    visual = np.pad(
                        visual,
                        ((max(0, -start), max(0, start + length - n)), (0, 0), (0, 0), (0, 0)),
                        mode="edge",
                    )
                assert len(visual) == length
                index = np.clip(np.arange(start, start + length), 0, n - 1)
                mid = np.rint((meta["pts"][index] - float(meta["audio_start"])) * 100).astype(int)
                audio = meta["mel"][np.clip(mid, 0, len(meta["mel"]) - 1)].astype(np.float32)
                yield dict(
                    images=torch.from_numpy(visual),
                    audio=torch.from_numpy(audio),
                    audio_mask=torch.tensor(float(meta["has_audio"])),
                    fps=torch.tensor(row["fps"], dtype=torch.float32),
                    start=start,
                )