"""AdaSpot-derived dense audiovisual Minecraft event spotter."""

import copy, math
import timm, torch
from torch import nn
from gsf import _GSF


class Shift(nn.Module):
    def __init__(self, conv, length):
        super().__init__()
        self.fold = math.ceil(conv.conv.in_channels // 4 / 4) * 4
        self.gs = _GSF(self.fold, length, 100)
        self.net = conv

    def forward(self, x):
        return self.net(torch.cat((self.gs(x[:, : self.fold]), x[:, self.fold :]), dim=1))


class Spotter(nn.Module):
    def __init__(self, length=96, pretrained=True, backbone="regnety_002", visual_stride=1):
        super().__init__()
        self.length = length
        self.visual_stride = visual_stride
        assert length % visual_stride == 0
        net = timm.create_model(backbone, pretrained=pretrained, num_classes=0)
        d = net.num_features
        for stage in [net.s1, net.s2, net.s3, net.s4]:
            for block in stage.children():
                block.conv1 = Shift(block.conv1, length // visual_stride)
        self.global_net = net
        self.detail_net = copy.deepcopy(net)
        self.global_proj = nn.Sequential(nn.Linear(d, d // 2), nn.ReLU(), nn.Linear(d // 2, d))
        self.detail_proj = copy.deepcopy(self.global_proj)
        self.audio = nn.Sequential(
            nn.Conv1d(64, 96, 5, padding=2), nn.GELU(), nn.Conv1d(96, d, 5, padding=2), nn.GELU()
        )
        self.gate = nn.Sequential(nn.Linear(d * 2 + 1, d), nn.Sigmoid())
        self.temporal = nn.GRU(d + 1, d, batch_first=True, bidirectional=True)
        self.action = nn.Linear(d * 2, 2)
        self.category = nn.ModuleList(
            [
                nn.Sequential(nn.Linear(d * 3, d), nn.ReLU(), nn.Dropout(0.2), nn.Linear(d, 8))
                for _ in range(2)
            ]
        )
        self.aux_temporal = nn.ModuleList(
            [nn.GRU(d, d, batch_first=True, bidirectional=True) for _ in range(2)]
        )
        self.aux_head = nn.ModuleList([nn.Linear(d * 2, 2) for _ in range(2)])
        self.register_buffer("mean", torch.tensor([0.485, 0.456, 0.406])[None, :, None, None])
        self.register_buffer("std", torch.tensor([0.229, 0.224, 0.225])[None, :, None, None])
        nn.init.constant_(self.action.bias, -4)
        for h in self.aux_head:
            nn.init.constant_(h.bias, -4)

    def forward(self, images, audio, audio_mask, fps, auxiliary=True, return_features=False):
        b, t, h, w, c = images.shape
        assert t == self.length
        visual = images[:, :: self.visual_stride]
        vt = visual.shape[1]
        x = visual.reshape(b * vt, h, w, c).permute(0, 3, 1, 2).float().div_(255)
        if self.training:

            gain = torch.empty(b, 1, 1, 1, 1, device=x.device).uniform_(0.8, 1.2)
            x = (x.reshape(b, vt, 3, h, w) * gain).clamp(0, 1).flatten(0, 1)
        x = (x - self.mean) / self.std
        g = self.global_net(
            x[:, :, :128, :224].contiguous(memory_format=torch.channels_last)
        ).reshape(b, vt, -1)
        c = self.detail_net(x[:, :, :, 224:].contiguous(memory_format=torch.channels_last)).reshape(
            b, vt, -1
        )
        if self.visual_stride > 1:

            position = torch.arange(t, device=x.device).float() / self.visual_stride
            left = position.long().clamp(max=vt - 1)
            right = (left + 1).clamp(max=vt - 1)
            alpha = (position - left).clamp(0, 1)[None, :, None]
            g = g[:, left] * (1 - alpha) + g[:, right] * alpha
            c = c[:, left] * (1 - alpha) + c[:, right] * alpha
        v = torch.maximum(self.global_proj(g), self.detail_proj(c))
        a = self.audio(((audio.float() + 12) / 8).transpose(1, 2)).transpose(1, 2)
        mask = audio_mask[:, None, None].expand(b, t, 1)
        if self.training:
            mask = mask * (torch.rand(b, 1, 1, device=x.device) > 0.25)
        gate = self.gate(torch.cat([v, a, mask], dim=-1)) * mask
        fused = v + gate * a
        fr = (fps / 60).view(b, 1, 1).expand(b, t, 1)

        with torch.autocast("cuda", enabled=False):
            temporal = self.temporal(torch.cat([fused, fr], -1).float())[0]
        logits = self.action(temporal)
        before = torch.cat([c[:, :1].expand(-1, 2, -1), c[:, :-2]], 1)
        after = torch.cat([c[:, 2:], c[:, -1:].expand(-1, 2, -1)], 1)
        cats = torch.stack(
            [
                head(torch.cat([temporal, context], -1))
                for head, context in zip(self.category, [before, after])
            ],
            2,
        )
        aux = []
        if auxiliary:
            for features, rnn, head in zip([g, c], self.aux_temporal, self.aux_head):
                with torch.autocast("cuda", enabled=False):
                    z = rnn(features.float())[0]
                aux.append(head(z))
        if return_features:
            return logits, cats, aux, torch.cat([temporal, c], -1)
        return logits, cats, aux
