import tempfile,unittest,wave
from pathlib import Path
import numpy as np
from backend.edit import select_events
from backend.audio_peaks import frame_peaks,inventory
from test_edit import data,event

class AdvancedTests(unittest.TestCase):
    def peaks(self):return np.zeros((1000,3),np.float32)
    def test_peak_search_and_handles(self):
        d=data([event(200)]);p=self.peaks();p[202]=.8;p[220]=1
        cut=select_events(d,dict(max_audio=True,before=3,after=6,peak_before=1,peak_after=2),p)
        self.assertEqual(cut['ranges'],[(201,204)])
        self.assertEqual(cut['events'][0]['peak_frame'],202)
        self.assertEqual(cut['events'][0]['frame_index'],200)
    def test_window_end_exclusive_ties_and_clamp(self):
        p=self.peaks();p[205]=.8;p[206]=1;p[201]=.8
        cut=select_events(data([event(200)]),dict(max_audio=True,peak_before=10,peak_after=10,peak_clamp=True),p)
        self.assertEqual(cut['events'][0]['peak_frame'],201)
        self.assertEqual(cut['ranges'],[(197,206)])
    def test_selection_boundaries(self):
        p=self.peaks();p[200]=1
        cut=select_events(data([event(200)]),dict(max_audio=True,start_frame=200,end_frame=205,peak_before=10,peak_after=10),p)
        self.assertEqual(cut['ranges'],[(200,205)])
    def test_quiet_fallback_skip_and_channel(self):
        p=self.peaks();p[201,0]=p[201,2]=1;p[203,1]=.5;d=data([event(200)])
        self.assertEqual(select_events(d,dict(max_audio=True,peak_channel='left'),p)['events'][0]['peak_frame'],203)
        p[:]=0
        self.assertEqual(select_events(d,dict(max_audio=True),p)['ranges'],[(197,206)])
        self.assertEqual(select_events(d,dict(max_audio=True,quiet_policy='skip'),p)['clips'],0)
    def test_alignment_duplicates(self):
        p=self.peaks();p[202]=1;d=data([event(200,.6),event(203,.9)])
        o=dict(max_audio=True,before=4,after=5,peak_before=1,peak_after=2)
        cut=select_events(d,o,p)
        self.assertEqual(cut['ranges'],[(201,204)]);self.assertEqual(cut['duplicates_removed'],1)
        self.assertEqual(select_events(d,dict(o,duplicates='all'),p)['clips'],2)
    def test_pacing_sampling_limit_reverse(self):
        d=data([event(i) for i in [20,40,100,180,260,340,420]],rate='60')
        cut=select_events(d,dict(min_gap=.5,every=2,offset=1,max_clips=2,order='reverse'))
        self.assertEqual([e['frame_index'] for e in cut['events']],[260,100]);self.assertEqual(cut['spacing_removed'],1)
    def test_limit_spread_duration_and_duplicate_choice(self):
        d=data([event(i) for i in [20,100,180,260,340]],rate='60')
        cut=select_events(d,dict(max_clips=3,limit_mode='even'))
        self.assertEqual([e['frame_index'] for e in cut['events']],[20,180,340])
        cut=select_events(d,dict(max_seconds=.31,order='reverse'))
        self.assertEqual([e['frame_index'] for e in cut['events']],[340,260])
        self.assertEqual(cut['output_frames'],18)
        d=data([event(20,.6),event(22,.9)])
        self.assertEqual(select_events(d,dict(duplicate_winner='first'))['events'][0]['frame_index'],20)
        self.assertEqual(select_events(d,dict(duplicate_winner='last'))['events'][0]['frame_index'],22)
    def test_invalid_options(self):
        for o in [dict(min_gap=-1),dict(max_clips=1.5),dict(order='random'),dict(max_audio=True,peak_before=0,peak_after=0),dict(peak_channel='bad')]:
            with self.assertRaises(ValueError):select_events(data([event(20)]),o,self.peaks())
    def test_streaming_antiphase_fractional_frames_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);source=tmp/'sound.wav';samples=np.zeros((96000,2),dtype=np.int16)
            samples[48000]=[30000,-30000];samples[60000]=[16000,0]
            with wave.open(str(source),'wb') as w:
                w.setnchannels(2);w.setsampwidth(2);w.setframerate(48000);w.writeframes(samples.tobytes())
            d=dict(frames=120,fps=60000/1001,rate='60000/1001',duration=2,source_start=0)
            peaks,channels=frame_peaks(source,d,0,tmp,lambda *a,**k:None)
            self.assertEqual(channels,2);self.assertEqual(int(peaks[:,0].argmax()),59)
            self.assertAlmostEqual(float(peaks[59,0]),30000/32768,places=5)
            self.assertEqual(float(peaks[59,1]),float(peaks[59,2]))
            cached,_=frame_peaks(source,d,0,tmp,lambda *a,**k:self.fail('Cache decoded'))
            np.testing.assert_array_equal(peaks,cached)
            shifted,_=frame_peaks(source,dict(d,source_start=.5),0,tmp,lambda *a,**k:None)
            self.assertEqual(int(shifted[:,0].argmax()),29)
            self.assertEqual(inventory(source)[0]['channels'],2)
            with self.assertRaises(ValueError):frame_peaks(source,d,1,tmp,lambda *a,**k:None)

if __name__=='__main__': unittest.main()