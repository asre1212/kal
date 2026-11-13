import unittest,json,sys,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from engine import initial_state,apply_allocation,summarize
class AllocationTests(unittest.TestCase):
 def setUp(self):
  self.c=json.loads((Path(__file__).resolve().parents[1]/'data/config.json').read_text());self.s=initial_state(0,self.c)
 def test_conservation_idempotence_and_history(self):
  old=copy.deepcopy(self.s['equity_history']);self.assertTrue(apply_allocation(self.s,self.c,10))
  self.assertEqual([self.s['buckets'][h+'_maker']['allocated_capital'] for h in ['day','month','long']],[22500,11250,11250])
  self.assertEqual(sum(b['cash'] for b in self.s['buckets'].values()),90000)
  self.assertEqual(self.s['equity_history'],old);self.assertFalse(apply_allocation(self.s,self.c,20))
  result=summarize(self.s,self.c,{},20);self.assertEqual(result['net_pnl'],0)
  self.assertTrue(all(b['net_pnl']==0 and b['initial']==15000 for b in result['buckets']))
 def test_blocked_is_atomic(self):
  self.s['buckets']['month_maker']['cash']=100;old=copy.deepcopy(self.s)
  with self.assertRaises(ValueError):apply_allocation(self.s,self.c,10)
  self.assertEqual(self.s,old)
 def test_reserved_cash_protected(self):
  self.s['trades']=[{'bucket':'long_maker','status':'entering','reserved':12000}];old=copy.deepcopy(self.s)
  with self.assertRaises(ValueError):apply_allocation(self.s,self.c,10)
  self.assertEqual(self.s,old)
