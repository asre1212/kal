"""Prospective, matched-entry exit experiments, isolated from the funded paper ledger.

Each policy is an alternative use of the SAME entry, never additive portfolio P&L.
Targets recover a tranche's whole basket cost plus profit from a winning leg.
Residual legs require an observed book. No synthetic historical market exits.
"""
import copy
import math
from collections import defaultdict
from engine import money, iso, ts, ledger_fill, mark_trade, close_at_book, finalize
from replay import arm_exits, replay_trade, cutoff


def initialize(state, cfg, now):
    if not cfg.get('profit_study', {}).get('enabled'):
        return None
    return state.setdefault('profit_study', {
        'version': cfg['profit_study']['version'], 'started_at': iso(now),
        'policies': copy.deepcopy(cfg['profit_study']['policies']),
        'cohorts': [], 'excluded': [], 'health': {},
        'method': 'Matched prospective entries; each policy is an alternative, not extra capital. '
                  'Resting sale limits recover each tranche’s entire entry cost plus its target from one leg. '
                  'Unsold legs exit at the next observed bid. All entry and exit fees count. '
                  'Targets are not capped or guaranteed. Stops/time exits use snapshots; settlement is a fallback. '
                  '5% public-print participation estimates fills; queue position is unknown.'})


def active_slices(state):
    return [t for c in state.get('profit_study', {}).get('cohorts', [])
            for v in c['variants'] for t in v['slices'] if t['status'] != 'closed']


def holder(state):
    # Cash only collects hypothetical proceeds; never feeds the real paper buckets.
    return {'buckets': {'study': {'cash': 0}}, 'trial_end': state['trial_end'],
            'updated_at': state['updated_at']}


def enroll(state, cfg, now):
    study = initialize(state, cfg, now)
    if study is None:
        return
    seen = {c['source_id'] for c in study['cohorts']} | {e['source_id'] for e in study['excluded']}
    for source in state['trades']:
        # Entry selection must have happened after the prospective study was registered.
        if source['id'] in seen or ts(source['created_at']) < ts(study['started_at']):
            continue
        if not source['cost'] or not all(l['bought'] == source['quantity'] for l in source['legs']):
            continue
        if source['quantity'] < 2:
            study['excluded'].append({'source_id': source['id'], 'reason': 'Fewer than 2 contracts per leg; cannot test a half exit.'})
            continue
        buys = [f for f in source['fills'] if f['action'] == 'buy']
        completed = max(ts(f['at']) for f in buys)
        # Sequential partial-entry fills that already had sales are not a common full entry.
        if any(ts(f['at']) <= completed for f in source['fills'] if f['action'] == 'sell'):
            study['excluded'].append({'source_id': source['id'], 'reason': 'Entry had already begun unwinding.'})
            continue
        cohort = {k: copy.deepcopy(source[k]) for k in ['event', 'horizon', 'mode', 'quantity', 'cost']}
        cohort.update(source_id=source['id'], entered_at=iso(completed), variants=[])
        for policy in study['policies']:
            variant = {'policy': policy['id'], 'slices': []}
            allocated_cost = allocated_fees = 0
            sizes = [source['quantity']] if len(policy['targets']) == 1 else [source['quantity']//2, source['quantity']-source['quantity']//2]
            entry_fees = money(sum(f['fee'] for f in buys))
            for i, (q, target) in enumerate(zip(sizes, policy['targets'])):
                weight = q/source['quantity']
                cost = money(source['cost']*weight) if i < len(sizes)-1 else money(source['cost']-allocated_cost)
                fees = money(entry_fees*weight) if i < len(sizes)-1 else money(entry_fees-allocated_fees)
                allocated_cost += cost; allocated_fees += fees
                t = {'id': f"{source['id']}:{policy['id']}:{i}", 'bucket': 'study',
                     'horizon': source['horizon'], 'mode': 'maker', 'entry_mode': source['mode'],
                     'version': study['version'], 'status': 'open', 'quantity': q, 'cost': cost,
                     'proceeds': 0, 'fees': fees, 'entry_fees': fees, 'cost_cap': source['cost_cap'],
                     'opened_at': source['opened_at'], 'entry_completed_at': iso(completed),
                     'replay_after': iso(completed), 'exit_target_ratio': target,
                     'fills': [], 'legs': [], 'mark': 0, 'pnl': 0, 'reserved': 0}
                for leg in source['legs']:
                    l = {k: copy.deepcopy(leg[k]) for k in ['ticker', 'side', 'close_time', 'decision_time', 'fee_multiplier', 'maker_enabled'] if k in leg}
                    l.update(bought=q, sold=0)
                    t['legs'].append(l)
                arm_exits(holder(state), t, cfg, completed)
                variant['slices'].append(t)
            cohort['variants'].append(variant)
        study['cohorts'].append(cohort)


def settle(state, t, market_map, now):
    for leg in t['legs']:
        m = market_map.get(leg['ticker'])
        if not m or m.get('status') not in ['settled', 'finalized']:
            continue
        result = m.get('result')
        if result == 'scalar' and m.get('settlement_value_dollars') is not None:
            p = float(m['settlement_value_dollars'])
            p = p if leg['side'] == 'yes' else 1-p
        elif result in ['yes', 'no']:
            p = float(result == leg['side'])
        else:
            continue
        q = leg['bought']-leg['sold']
        if q:
            ledger_fill(state, t, leg, 'sell', p, q, 'settlement', now, 'Official settlement fallback; early exit failed')
            t['settlement_fallback'] = True


def advance(state, books, markets, now, cfg, fetch_history, cached_prints=None):
    enroll(state, cfg, now)
    study = state.get('profit_study')
    if study is None:
        return {}
    active = active_slices(state)
    requests = {}
    for t in active:
        for l in t['legs']:
            requests[l['ticker']] = min(requests.get(l['ticker'], now), ts(t['replay_after']))
    history = {}; evidence = {}; failures = []
    for ticker, after in requests.items():
        cache = [v for (tk, start), v in (cached_prints or {}).items()
                 if tk == ticker and ts(start) <= after and v is not None]
        try:
            rows = cache[0] if cache else fetch_history(ticker, after, now)
            if rows is None:
                raise ValueError('History unavailable')
            history[ticker] = rows
            if not cache:
                evidence[(ticker, iso(after))] = rows
        except Exception as exc:
            failures.append({'ticker': ticker, 'error': str(exc)})
    md = {m['ticker']: m for m in markets}
    # Independent counterfactual policies each receive the same liquidity budget.
    # Within a policy, all sources and tranches share that budget.
    used = defaultdict(dict); book_used = defaultdict(dict)
    sandbox = holder(state)
    blocked = 0
    for cohort in study['cohorts']:
        for variant in cohort['variants']:
            policy = variant['policy']
            for t in variant['slices']:
                if t['status'] == 'closed':
                    continue
                if any(l['ticker'] not in history for l in t['legs']):
                    t['backfill_status'] = 'incomplete: checkpoint retained'; blocked += 1
                    mark_trade(t, books)
                    continue
                replay_trade(sandbox, t, history, now, cfg, used[policy])
                t['backfill_status'] = 'complete'
                if t['status'] == 'closed':
                    continue
                settle(sandbox, t, md, now)
                mark_trade(t, books)
                reason = t.get('exit_reason') if t['status'] == 'closing' else None
                if now >= cutoff(sandbox, t, cfg):
                    reason = 'Time exit at next observed book'
                    t['time_exit_lag_hours'] = round((now-cutoff(sandbox, t, cfg))/3600, 3)
                elif not t['unpriced'] and not t['depth_shortfall'] and t['pnl'] <= -cfg['loss_trigger']*t['cost']:
                    reason = 'Loss trigger at observed book'
                elif t.get('profit_recovered_at'):
                    reason = 'Target recovered; close residual at observed book'
                if reason:
                    # Reserve displayed depth across all sources/tranches in this policy.
                    available = copy.deepcopy(books)
                    for ticker, b in available.items():
                        for side in ['yes', 'no']:
                            b[side] = [[p, max(0, math.floor(size*cfg['participation'])-book_used[policy].get((ticker, side, p), 0))] for p, size in b[side]]
                    n = len(t['fills'])
                    close_at_book(sandbox, t, available, now, reason, 1)
                    for f in t['fills'][n:]:
                        key = (f['ticker'], f['side'], f['price'])
                        book_used[policy][key] = book_used[policy].get(key, 0)+f['quantity']
                finalize(t, now)
                if t['status'] != 'closed':
                    mark_trade(t, books)
    study['health'] = {'at': iso(now), 'failed_histories': failures, 'blocked_slices': blocked}
    refresh_summary(state)
    return evidence


def variant_result(cohort, variant):
    slices = variant['slices']
    closed = all(t['status'] == 'closed' for t in slices)
    fees = money(sum(t['fees'] for t in slices))
    cost = money(sum(t['cost'] for t in slices))
    proceeds = money(sum(t['proceeds'] for t in slices))
    mark = money(sum(t['mark'] for t in slices if t['status'] != 'closed'))
    deadline = min(ts(l.get('decision_time', l['close_time'])) for t in slices for l in t['legs'])
    ended = max(ts(t['closed_at']) for t in slices) if closed else None
    fallback = any(t.get('settlement_fallback') for t in slices)
    return {'source_id': cohort['source_id'], 'policy': variant['policy'], 'event': cohort['event'],
            'horizon': cohort['horizon'], 'entry_mode': cohort['mode'], 'cost': cost, 'fees': fees,
            'proceeds': proceeds, 'mark': mark, 'net_pnl': money(proceeds+mark-cost),
            'closed': closed, 'closed_at': iso(ended) if closed else None,
            'hold_hours': (ended-ts(cohort['entered_at']))/3600 if closed else None,
            'early_exit': closed and not fallback and ended < deadline,
            'settlement_fallback': fallback,
            'late_exit': any(t.get('time_exit_lag_hours', 0) > 0 for t in slices),
            'unreachable': any(l.get('target_unreachable') for t in slices for l in t['legs']),
            'valuation_incomplete': any(t.get('unpriced') or t.get('depth_shortfall') for t in slices if t['status'] != 'closed'),
            'history_incomplete': any(t.get('backfill_status', '').startswith('incomplete') for t in slices),
            'target_recovered_tranches': sum(t['proceeds'] >= money(t['cost']*(1+t['exit_target_ratio'])) for t in slices),
            'tranches': len(slices)}


def refresh_summary(state):
    study = state.get('profit_study')
    if not study:
        return
    results = [variant_result(c, v) for c in study['cohorts'] for v in c['variants']]
    # Compare every alternative on exactly the same fully resolved entries.
    complete = {c['source_id'] for c in study['cohorts'] if all(all(t['status'] == 'closed' for t in v['slices']) for v in c['variants'])}
    study['results'] = results
    for result in results:
        variant=next(v for c in study['cohorts'] if c['source_id']==result['source_id'] for v in c['variants'] if v['policy']==result['policy'])
        result['nonsettlement_sold']=sum(f['quantity'] for t in variant['slices'] for f in t['fills'] if f['action']=='sell' and f['mode']!='settlement')
    study['matched_complete_ids'] = sorted(complete)
    study['event_blocks'] = len({r['event'] for r in results if r['source_id'] in complete})
    study['evidence'] = 'Exploratory comparisons; no automatic winner. Require at least 20 independent event blocks, paired uncertainty, costs and execution sensitivity before changing the baseline.'
    if state.get('config',{}).get('profit_study'):
        from profit_stats import analyze
        study['analysis']=analyze(study,state['config'])
