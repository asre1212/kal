import unittest,sys,copy,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from engine import *
from replay import replay_trade,arm_exits
from collect import prints_since,process

class ReplayTests(unittest.TestCase):
 def setup_case(self):
  cfg=json.loads((Path(__file__).resolve().parents[1]/'data/config.json').read_text())
  cfg['horizons']['day']['max_hold_hours']=2
  state=initial_state(0,cfg)
  leg={'ticker':'X','side':'yes','bought':0,'sold':0,'limit':.2,'close_time':iso(86400),'fee_multiplier':1,'maker_enabled':False,'buy_order':{'side':'yes','action':'buy','limit':.2,'after':iso(0)}}
  trade={'id':'T1','bucket':'day_maker','horizon':'day','mode':'maker','version':'v2','status':'entering','quantity':10,'cost':0,'proceeds':0,'cost_cap':2000,'fees':0,'fills':[],'reserved':2,'created_at':iso(0),'opened_at':iso(0),'entry_deadline':iso(3600),'legs':[leg]}
  state['trades']=[trade];return state,trade,leg,cfg
 def print(self,id,at,yes,count=200,aggressor='no'):
  return {'trade_id':id,'created_time':iso(at),'yes_price_dollars':str(yes),'no_price_dollars':str(1-yes),'count_fp':str(count),'taker_side':aggressor,'ticker':'X'}
 def test_entry_then_exit_within_twelve_hours(self):
  s,t,l,c=self.setup_case();history={'X':[self.print('sell',1000,.25,aggressor='yes'),self.print('buy',100,.2)]}
  replay_trade(s,t,history,43200,c,{})
  self.assertEqual(t['status'],'closed');self.assertEqual([x['at'] for x in t['fills']],[iso(100),iso(1000)])
  self.assertEqual(t['pnl'],.5);self.assertEqual(t['closed_at'],iso(1000))
 def test_sell_before_buy_not_used(self):
  s,t,l,c=self.setup_case();replay_trade(s,t,{'X':[self.print('sell',50,.9,aggressor='yes'),self.print('buy',100,.2)]},43200,c,{})
  self.assertEqual(l['sold'],0);self.assertEqual(l['bought'],10)
 def test_same_timestamp_not_used_for_contingent_exit(self):
  s,t,l,c=self.setup_case();replay_trade(s,t,{'X':[self.print('a',100,.2),self.print('z',100,.9,aggressor='yes')]},43200,c,{})
  self.assertEqual(l['sold'],0)
 def test_no_fills_after_order_expiry(self):
  s,t,l,c=self.setup_case();replay_trade(s,t,{'X':[self.print('late',3601,.1)]},43200,c,{})
  self.assertEqual(l['bought'],0)
 def test_duplicate_prints_and_idempotence(self):
  s,t,l,c=self.setup_case();p=self.print('one',100,.2,count=100)
  replay_trade(s,t,{'X':[p,p]},200,c,{})
  self.assertEqual(l['bought'],5)
  replay_trade(s,t,{'X':[p]},400,c,{})
  self.assertEqual(l['bought'],5)
 def test_does_not_resell_after_time_cutoff(self):
  s,t,l,c=self.setup_case();replay_trade(s,t,{'X':[self.print('buy',100,.2),self.print('late',7201,.9,aggressor='yes')]},43200,c,{})
  self.assertEqual(l['sold'],0)
 def test_partial_volume_carries_over(self):
  s,t,l,c=self.setup_case();replay_trade(s,t,{'X':[self.print('a',100,.2,count=10)]},200,c,{})
  self.assertEqual(l['bought'],0)
  replay_trade(s,t,{'X':[self.print('b',300,.2,count=10)]},400,c,{})
  self.assertEqual(l['bought'],1)
 def test_complete_pagination_deduplicates(self):
  p=self.print('a',100,.2)
  with patch('collect.get',side_effect=[{'trades':[p],'cursor':'next'},{'trades':[p],'cursor':''}]):
   self.assertEqual(len(prints_since('X',0,200)),1)
 def test_incomplete_history_preserves_checkpoint(self):
  s,t,l,c=self.setup_case()
  with patch('collect.prints_since',side_effect=RuntimeError('source incomplete')):
   process(s,{},[],43200,c)
  self.assertEqual(t['replay_after'],iso(0));self.assertEqual(l['bought'],0);self.assertEqual(t['status'],'entering')
  self.assertEqual(s['backfill']['failed_trades'],1)
 def test_settlement_after_historical_limit_exit(self):
  s,t,l,c=self.setup_case()
  with patch('collect.prints_since',return_value=[self.print('buy',100,.2),self.print('sell',200,.25,aggressor='yes')]):
   process(s,{},[{'ticker':'X','status':'settled','result':'no'}],43200,c)
  self.assertEqual(t['pnl'],.5);self.assertEqual(len(t['fills']),2)
if __name__=='__main__':unittest.main()
