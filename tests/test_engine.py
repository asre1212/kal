import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from engine import *
class LedgerTests(unittest.TestCase):
 def test_reciprocal_depth(self):
  b={'yes':[[.4,20]],'no':[[.55,10],[.5,20]]}
  self.assertEqual(execution(b,'yes','buy',25),[(.45,10),(.5,15)])
  self.assertEqual(quote(b,'yes','sell'),(.4,20))
 def test_fee(self):
  self.assertEqual(fee(.5,100),1.75)
  self.assertEqual(fee(.5,100,'maker'),0)
  self.assertEqual(fee(.5,100,'maker',1,True),.4375)
 def setup_ledger(self):
  return {'buckets':{'x':{'cash':100000}}},{'bucket':'x','cost':0,'proceeds':0,'cost_cap':2000,'fees':0,'fills':[]},{'ticker':'x','side':'yes','bought':0,'sold':0}
 def test_combined_trade_cap(self):
  s,t,l=self.setup_ledger();ledger_fill(s,t,l,'buy',.5,2000,'taker',0,'test')
  with self.assertRaises(ValueError):ledger_fill(s,t,l,'buy',.5,2000,'taker',0,'second leg')
 def test_round_trip_cash(self):
  s,t,l=self.setup_ledger();ledger_fill(s,t,l,'buy',.2,100,'taker',0,'test');ledger_fill(s,t,l,'sell',.3,100,'taker',60,'test')
  self.assertAlmostEqual(s['buckets']['x']['cash'],100007.41)
  self.assertAlmostEqual(t['proceeds']-t['cost'],7.41)
 def test_maker_touch_does_not_fill(self):
  order={'side':'yes','action':'buy','limit':.5,'after':iso(100)}
  p={'created_time':iso(200),'yes_price_dollars':'.5','count_fp':'1000','taker_side':'no'}
  self.assertEqual(observed_maker_fills(order,[p],100),0)
  p['yes_price_dollars']='.49';self.assertEqual(observed_maker_fills(order,[p],100),50)
  p['created_time']=iso(99);self.assertEqual(observed_maker_fills(order,[p],100),0)
 def test_no_overselling(self):
  s,t,l=self.setup_ledger()
  with self.assertRaises(ValueError):ledger_fill(s,t,l,'sell',.5,1,'taker',0,'test')
 def test_missing_marks_not_optimistic(self):
  t={'cost':100,'proceeds':0,'legs':[{'ticker':'x','side':'yes','bought':100,'sold':0}]}
  self.assertEqual(mark_trade(t,{}),0);self.assertTrue(t['unpriced'])
 def test_settlement_no_fee(self):
  s,t,l=self.setup_ledger();ledger_fill(s,t,l,'buy',.5,100,'maker',0,'test');ledger_fill(s,t,l,'sell',1,100,'settlement',60,'final')
  self.assertEqual(s['buckets']['x']['cash'],100050)
 def test_total_capital(self):
  import json
  c=json.loads((Path(__file__).resolve().parents[1]/'data/config.json').read_text());s=initial_state(0,c)
  self.assertEqual(sum(b['cash'] for b in s['buckets'].values())+c['reserve'],100000)
if __name__=='__main__':unittest.main()
