import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from engine import initial_state,iso,ledger_fill
from profit_study import initialize,enroll,advance,refresh_summary
from profit_stats import holm,paired_bootstrap,wilson

class ProfitStudyTests(unittest.TestCase):
 def case(self,qty=10,price=.2,legs=1):
  cfg=json.loads((Path(__file__).resolve().parents[1]/'data/config.json').read_text());s=initial_state(0,cfg);s['config']=cfg;initialize(s,cfg,0)
  t={'id':'T1','event':'E1','bucket':'day_taker','horizon':'day','mode':'taker','version':'original','quantity':qty,'cost':0,'proceeds':0,'cost_cap':2000,'fees':0,'fills':[],'opened_at':iso(10),'created_at':iso(10),'status':'open','legs':[]}
  for i in range(legs):
   l={'ticker':f'X{i}','side':'yes','bought':0,'sold':0,'close_time':iso(86400),'decision_time':iso(86400),'fee_multiplier':1,'maker_enabled':False};t['legs'].append(l);ledger_fill(s,t,l,'buy',price,qty,'taker',10,'Test fixture')
  s['trades']=[t];return s,t,cfg
 def print(self,id,at,price,count=10000):
  return {'trade_id':id,'created_time':iso(at),'yes_price_dollars':str(price),'no_price_dollars':str(1-price),'count_fp':str(count),'taker_side':'yes'}
 def test_study_never_changes_funded_portfolio(self):
  s,t,c=self.case();original=copy.deepcopy((s['buckets'],s['trades']));advance(s,{},[],100,c,lambda *x:[self.print('p',50,.9)])
  self.assertEqual((s['buckets'],s['trades']),original);self.assertEqual(len(s['profit_study']['cohorts'][0]['variants']),5)
  self.assertEqual(len(s['profit_study']['matched_complete_ids']),1)
 def test_entry_cost_fees_and_odd_quantities_preserved(self):
  s,t,c=self.case(qty=7,legs=2);enroll(s,c,20)
  for v in s['profit_study']['cohorts'][0]['variants']:
   self.assertAlmostEqual(sum(x['cost'] for x in v['slices']),t['cost']);self.assertAlmostEqual(sum(x['fees'] for x in v['slices']),t['fees'])
   self.assertEqual(sum(x['quantity'] for x in v['slices']),7)
  self.assertEqual([x['quantity'] for x in s['profit_study']['cohorts'][0]['variants'][-1]['slices']],[3,4])
 def test_no_retroactive_or_tiny_entry_enrollment(self):
  s,t,c=self.case(qty=1);enroll(s,c,20);self.assertEqual(len(s['profit_study']['cohorts']),0);self.assertEqual(len(s['profit_study']['excluded']),1)
  s,t,c=self.case();s['profit_study']['started_at']=iso(11);enroll(s,c,20);self.assertEqual(len(s['profit_study']['cohorts']),0)
 def test_backfill_cannot_sell_at_entry_timestamp(self):
  s,t,c=self.case();advance(s,{},[],100,c,lambda *x:[self.print('p',10,.9)])
  self.assertTrue(all(not r['closed'] for r in s['profit_study']['results']))
 def test_staged_orders_share_volume_and_do_not_oversell(self):
  s,t,c=self.case();advance(s,{},[],100,c,lambda *x:[self.print('p',50,.9,100)])
  v=s['profit_study']['cohorts'][0]['variants'][-1]
  self.assertEqual(sum(x['legs'][0]['sold'] for x in v['slices']),5)
  advance(s,{},[],200,c,lambda *x:[self.print('p',50,.9,100)])
  self.assertEqual(sum(x['legs'][0]['sold'] for x in v['slices']),5)
 def test_fixed_targets_are_not_silently_capped(self):
  s,t,c=self.case(price=.9);enroll(s,c,20);v=s['profit_study']['cohorts'][0]['variants'][3]['slices'][0]
  self.assertEqual(v['target_ratio'],.25);self.assertTrue(v['legs'][0]['target_unreachable']);self.assertNotIn('sell_order',v['legs'][0])
 def test_one_winning_leg_target_covers_whole_basket(self):
  s,t,c=self.case(legs=2);advance(s,{},[],100,c,lambda ticker,*x:[self.print('p',50,.9)] if ticker=='X0' else [])
  for v in s['profit_study']['cohorts'][0]['variants']:
   for part in v['slices']:
    self.assertGreaterEqual(part['proceeds'],part['cost']*(1+part['exit_target_ratio'])-1e-4)
    self.assertEqual(part['legs'][1]['sold'],0)
 def test_incomplete_history_checkpoint_preserved(self):
  s,t,c=self.case();enroll(s,c,20);part=s['profit_study']['cohorts'][0]['variants'][0]['slices'][0];before=part['replay_after']
  def fail(*x):raise RuntimeError('missing')
  advance(s,{},[],100,c,fail);self.assertEqual(part['replay_after'],before);self.assertEqual(part['legs'][0]['sold'],0)
 def test_no_old_print_used_after_time_cutoff(self):
  s,t,c=self.case();advance(s,{},[],8000,c,lambda *x:[self.print('late',7500,.9)])
  self.assertTrue(all(not r['closed'] for r in s['profit_study']['results']))
  self.assertTrue(all(r['late_exit'] for r in s['profit_study']['results']))
 def test_settlement_fallback_and_common_completed_sample(self):
  s,t,c=self.case();advance(s,{},[],100,c,lambda *x:[self.print('p',50,.23)])
  self.assertEqual(s['profit_study']['matched_complete_ids'],[])
  advance(s,{},[{'ticker':'X0','status':'settled','result':'no'}],90000,c,lambda *x:[])
  rs=s['profit_study']['results'];self.assertTrue(all(r['closed'] for r in rs));self.assertTrue(any(r['settlement_fallback'] for r in rs));self.assertTrue(all(not r['early_exit'] for r in rs if r['settlement_fallback']))
 def test_common_marks_exclude_missing_in_any_alternative(self):
  s,t,c=self.case();advance(s,{},[],100,c,lambda *x:[self.print('p',50,.23)])
  rows=s['profit_study']['analysis']['strata'][0]['rows'];self.assertTrue(all(r['marked_net_pnl'] is None for r in rows));self.assertTrue(all(r['matched_closed']==0 for r in rows))
 def test_holm_adjustment(self):
  a=holm({'a':.01,'b':.04,'c':.03});self.assertAlmostEqual(a['a'],.03);self.assertAlmostEqual(a['b'],.06);self.assertAlmostEqual(a['c'],.06)
 def test_paired_bootstrap_known_difference_and_correlated_blocks(self):
  rows=[{'source_id':str(i),'event':str(i),'cost':100,'net_pnl':20} for i in range(20)];base={r['source_id']:{**r,'net_pnl':10} for r in rows}
  stats=paired_bootstrap(rows,base,draws=2000);self.assertEqual(stats['return_ci'],[.2,.2]);self.assertEqual(stats['delta_ci'],[.1,.1]);self.assertLess(stats['p_raw'],.01)
  for r in rows:r['event']='same_event'
  self.assertIsNone(paired_bootstrap(rows,base)['return_ci'])
 def test_wilson_nonzero_uncertainty_at_all_wins(self):
  lo,hi=wilson(10,10);self.assertGreater(lo,.7);self.assertLess(lo,.8);self.assertAlmostEqual(hi,1)
if __name__=='__main__':unittest.main()
