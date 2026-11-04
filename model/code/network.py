"""Native-frame CTC event sequences with inexpensive full-rate motion evidence.

Separate break/place token streams support repeated, rapid events, including
two-frame spacing, with no fixed event-query cap or NMS radius. CTC decoding
collapses a contiguous event-token run; a learned blank separates real events.
"""

import torch
from torch import nn
from torch.nn import functional as F
from backbone import Spotter as Backbone

class ResidualTemporal(nn.Module):
    def __init__(self, dilation):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(256, 256, 3, padding=dilation, dilation=dilation, groups=256),
            nn.GELU(),
            nn.Conv1d(256, 256, 1),
            nn.Dropout(0.1),
        )
        self.norm = nn.LayerNorm(256)

    def forward(self, x):
        return self.norm((x + self.net(x)).transpose(1, 2)).transpose(1, 2)

class Spotter(nn.Module):
    def __init__(self, pretrained=True, visual_stride=2, length=96):
        super().__init__()
        self.length = length
        self.base = Backbone(length=length, pretrained=pretrained, visual_stride=visual_stride)
        d = self.base.global_net.num_features
        self.native = nn.Sequential(
            nn.Conv2d(6, 16, 5, stride=2, padding=2),
            nn.GELU(),
            nn.Conv2d(16, 32, 3, stride=2, padding=1),
            nn.GELU(),
            nn.Conv2d(32, 64, 3, stride=2, padding=1),
            nn.GELU(),
            nn.AdaptiveAvgPool2d(1),
        )
        self.proj = nn.Conv1d(d * 3 + 64, 256, 1)
        self.refine = nn.Sequential(*[ResidualTemporal(d) for d in [1, 2, 4, 8]])
        self.token = nn.Conv1d(256, 2, 1)
        self.category = nn.Linear(256, 16)
        nn.init.constant_(self.token.bias, -3)

    def forward(self, images, audio, audio_mask, fps, auxiliary=False):
        dense, cats, aux, features = self.base(
            images, audio, audio_mask, fps, auxiliary=auxiliary, return_features=True
        )
        b, t, h, w, c = images.shape
        x = images.flatten(0, 1).permute(0, 3, 1, 2).float() / 255
        x = F.interpolate(x, size=(40, 96), mode="bilinear", align_corners=False).reshape(
            b, t, 3, 40, 96
        )
        diff = x - torch.cat([x[:, :1], x[:, :-1]], 1)
        motion = self.native(torch.cat([x, diff], 2).flatten(0, 1)).reshape(b, t, 64)
        z = self.refine(self.proj(torch.cat([features, motion], -1).transpose(1, 2)))
        return dict(
            tokens=self.token(z).transpose(1, 2),
            categories=cats + self.category(z.transpose(1, 2)).reshape(b, t, 2, 8),
            dense=dense,
            aux=aux,
        )

def decode_sequence(logits, categories, threshold=0.5):
    import numpy as np

    prob = 1 / (1 + np.exp(-np.clip(np.asarray(logits, dtype=np.float32), -60, 60)))
    result = []
    for action in range(2):
        occupied = prob[:, action] >= threshold
        edges = np.diff(np.r_[False, occupied, False].astype(np.int8))
        for lo, hi in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            frame = int(lo + prob[lo:hi, action].argmax())
            result.append(
                (frame, action, int(categories[frame, action].argmax()), float(prob[frame, action]))
            )
    return sorted(result)