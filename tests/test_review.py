import unittest,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from collect import horizon,decision_time
from engine import ts
from review_stats import diagnostics
class ReviewTests(unittest.TestCase):
 def test_deadline_not_broad_settlement(self):
  cfg=json.loads((Path(__file__).resolve().parents[1]/'data/config.json').read_text())
  m={'ticker':'KXIMPEACH-27-AUG01','close_time':'2027-08-01T14:00:00Z','expected_expiration_time':'2028-02-01T15:00:00Z'}
  self.assertEqual(horizon(m,ts('2026-11-06T15:00:00Z'),cfg),'long')
 def test_same_day_calendar(self):
  cfg=json.loads((Path(__file__).resolve().parents[1]/'data/config.json').read_text())
  m={'ticker':'X','close_time':'2026-11-07T10:00:00Z'}
  self.assertIsNone(horizon(m,ts('2026-11-06T15:00:00Z'),cfg))
 def test_correlated_trades_are_one_block(self):
  state={'buckets':{'day_maker':{}},'trades':[{'bucket':'day_maker','version':'v1','status':'closed','cost':100,'pnl':10,'event':'same'} for _ in range(30)]}
  row=diagnostics(state)['rows'][0]
  self.assertEqual(row['event_blocks'],1);self.assertIsNone(row['return_ci'])
 def test_identical_returns_bootstrap(self):
  state={'buckets':{'day_maker':{}},'trades':[{'bucket':'day_maker','version':'v1','status':'closed','cost':100,'pnl':10,'event':str(i)} for i in range(20)]}
  self.assertEqual(diagnostics(state)['rows'][0]['return_ci'],[.1,.1])
if __name__=='__main__':unittest.main()
