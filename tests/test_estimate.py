import json,tempfile,unittest
from pathlib import Path
from backend.estimate import Estimate

class EstimateTests(unittest.TestCase):
    def test_whole_job_and_history(self):
        with tempfile.TemporaryDirectory() as folder:
            records=[];now=[0]
            meta=dict(duration=60,frames=3600,fps=60,codec='h264',width=1920,height=1080)
            e=Estimate(lambda kind,**p:records.append(p),folder,meta,'auto',8,peaks=True,clock=lambda:now[0])
            e('progress',stage='Preparing video',eta_seconds=10)
            self.assertGreater(records[-1]['eta_total_seconds'],10)
            now[0]=12;e('progress',stage='Preparing audio')
            self.assertLess(records[-1]['eta_total_seconds'],10)
            now[0]=14;e('progress',stage='Loading model')
            now[0]=15;e('progress',stage='Analyzing on CUDA',eta_seconds=2)
            self.assertGreater(records[-1]['eta_total_seconds'],2)
            now[0]=18;e('progress',stage='Finding audio peaks',eta_seconds=1)
            self.assertEqual(records[-1]['eta_total_seconds'],2)
            now[0]=20;e.save()
            history=json.loads((Path(folder)/'performance.json').read_text())
            self.assertTrue(any(k.startswith('infer:') for k in history))
    def test_cached_and_corrupt_history(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'performance.json').write_text('[]')
            results=[];e=Estimate(lambda kind,**p:results.append(p),folder,dict(duration=60,frames=3600,fps=60),'cpu',128,cached=True)
            e('progress',stage='Using cached analysis')
            self.assertEqual(results[-1]['eta_total_seconds'],1)

if __name__=='__main__':unittest.main()
