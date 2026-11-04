import json, tempfile, unittest
from pathlib import Path
from xml.etree import ElementTree as E
from backend.edit import select_events,write_xml,TICKS

def event(frame,confidence=.8,action='break',category='ore'):
    return dict(frame_index=frame,confidence=confidence,action=action,category=category)
def data(events=None,rate='60000/1001',channels=2):
    return dict(rate=rate,frames=1000,width=1920,height=1080,channels=channels,sample_rate=48000,
                edit_source=str(Path(tempfile.gettempdir())/'ore & café clip.mp4'),events=events or [])

class EditorialTests(unittest.TestCase):
    def test_single_clip_no_extension(self):
        d=data([event(20,.7),event(22,.9),event(100)])
        p=select_events(d,{})
        self.assertEqual(p['ranges'],[(19,28),(97,106)])
        self.assertEqual(p['duplicates_removed'],1)
        self.assertEqual(p['output_frames'],18)
    def test_every_n_after_filter_and_cleanup(self):
        d=data([event(20,.7),event(22,.9)]+[event(100+i*20) for i in range(20)]+[event(50,action='place')])
        p=select_events(d,{'every':10})
        self.assertEqual([e['frame_index'] for e in p['events']],[22,280,480])
    def test_categories_and_actions(self):
        d=data([event(10),event(50,action='place'),event(80,category='wood')])
        self.assertEqual(select_events(d,{'action':'place','categories':['ore']})['clips'],1)
        self.assertEqual(select_events(d,{'categories':['wood']})['events'][0]['frame_index'],80)
    def test_overlap_off(self):
        self.assertEqual(select_events(data([event(10),event(12)]),{'duplicates':'all'})['clips'],2)
    def test_boundaries_and_seconds(self):
        p=select_events(data([event(0),event(999)]),{'unit':'seconds','before':.1,'after':.1})
        self.assertEqual(p['ranges'],[(0,6),(993,1000)])
    def test_empty_and_invalid(self):
        self.assertEqual(select_events(data(),{})['clips'],0)
        for o in [{'categories':[]},{'every':0},{'before':float('nan')},{'after':1.5},{'before':0,'after':0}]:
            with self.assertRaises(ValueError):select_events(data(),o)
    def test_rational_rate_links_and_ticks(self):
        for rate,base,ntsc in [('60000/1001','60','TRUE'),('30000/1001','30','TRUE'),('60','60','FALSE')]:
            d=data([event(200),event(350)],rate)
            with tempfile.TemporaryDirectory() as tmp:
                f=Path(tmp)/'edit.xml';write_xml(d,select_events(d,{}),'Test & Mine',f)
                seq=E.parse(f).find('sequence')
                self.assertEqual(seq.findtext('rate/timebase'),base);self.assertEqual(seq.findtext('rate/ntsc'),ntsc)
                clips=seq.findall('.//clipitem');ids={c.get('id') for c in clips}
                self.assertEqual(len(clips),6)
                for c in clips:
                    self.assertEqual(int(c.findtext('out'))-int(c.findtext('in')),9)
                    self.assertTrue(all(x.text in ids for x in c.findall('link/linkclipref')))
                self.assertEqual(seq.findtext('media/audio/track/clipitem/sourcetrack/trackindex'),'1')
                self.assertEqual(seq.findall('media/audio/track')[1].findtext('clipitem/sourcetrack/trackindex'),'2')
                self.assertIn('%20',seq.findtext('.//file/pathurl'))
                if Path(d['edit_source']).drive:
                    self.assertTrue(seq.findtext('.//file/pathurl').startswith('file://localhost/'))
                    self.assertIn('%3A',seq.findtext('.//file/pathurl'))
    def test_silent_source(self):
        d=data([event(20)],channels=0)
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'edit.xml';write_xml(d,select_events(d,{}),'Silent',f)
            self.assertIsNone(E.parse(f).find('sequence/media/audio'))

if __name__=='__main__': unittest.main()
