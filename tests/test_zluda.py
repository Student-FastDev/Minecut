import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from backend.engine import MODEL_SHA, identity, zluda_device


class ZludaTests(unittest.TestCase):
    def test_requires_translated_device(self):
        torch = Mock()
        torch.cuda.is_available.return_value = False
        with self.assertRaisesRegex(RuntimeError, "ZLUDA"):
            zluda_device(torch)
        torch.cuda.get_device_name.assert_not_called()
        torch.cuda.is_available.return_value = True
        torch.cuda.get_device_name.return_value = "NVIDIA GeForce"
        with self.assertRaisesRegex(RuntimeError, "ZLUDA"):
            zluda_device(torch)
        torch.cuda.get_device_name.return_value = "AMD Radeon [ZLUDA]"
        self.assertEqual(zluda_device(torch), "AMD Radeon [ZLUDA]")

    def test_cache_separates_amd_and_preserves_native_results(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "capture.mp4"
            source.write_bytes(b"capture")
            baseline = identity(source, MODEL_SHA)
            for device in ("auto", "cpu", "cuda"):
                self.assertEqual(identity(source, MODEL_SHA, device=device), baseline)
            amd = identity(source, MODEL_SHA, device="zluda")
            self.assertNotEqual(amd, baseline)
            selection = {"start_seconds": 0, "end_seconds": 3}
            self.assertNotEqual(amd, identity(source, MODEL_SHA, selection, "zluda"))
