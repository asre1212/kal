import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from collect import enter
from audit_review import main

class OperationalReviewTests(unittest.TestCase):
    def test_paused_entries_do_not_touch_existing_ledger(self):
        cfg=json.loads((Path(__file__).resolve().parents[1]/'data/config.json').read_text())
        cfg['paused_buckets']=['day_maker','day_taker']
        state={'trades':[{'id':'existing','version':'original','fills':[{'quantity':3}]}]}
        original=copy.deepcopy(state)
        for mode in ['maker','taker']:enter(state,{'horizon':'day'},mode,{},0,cfg)
        self.assertEqual(state,original)

    def test_repository_ledger_and_source_evidence_reconcile(self):
        result=main()
        for key in ['accounting_errors','fee_errors','sequence_errors','cap_errors','missing_prints','split_errors','target_errors']:
            self.assertEqual(result['audit'][key],[],key)
        self.assertTrue(all(x==0 for x in result['audit']['cash_differences'].values()))
        self.assertTrue(result['diagnostics_reproduced'])
        self.assertTrue(result['profit_diagnostics_reproduced'])
