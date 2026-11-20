"""Read-only review audit. Run against the cited Git snapshot, never submits orders.

Usage: python scripts/audit_review.py > /tmp/review.json
The fixed Actions evidence file is specific to the November 20 review.
"""
import collections, copy, json, math, statistics
from pathlib import Path
from datetime import datetime, timezone
from engine import ts, money, fee, quote
from review_stats import diagnostics, quantile
from profit_stats import analyze, wilson

ROOT=Path(__file__).resolve().parents[1]

def stats(trades):
    closed=[t for t in trades if t['status']=='closed' and t['cost']>0]
    pos=[t['pnl'] for t in closed if t['pnl']>0]; neg=[t['pnl'] for t in closed if t['pnl']<0]
    cost=sum(t['cost'] for t in closed); pnl=sum(t['pnl'] for t in closed)
    groups=collections.defaultdict(float)
    for t in trades: groups[t['event']]+=t['cost']
    total=sum(groups.values()); weights=[v/total for v in groups.values()] if total else []
    ratios=[t['pnl']/t['cost'] for t in closed]; holds=[t['hold_hours'] for t in closed]
    lags=[t['time_exit_lag_hours'] for t in trades if t.get('time_exit_lag_hours',0)>1]
    return dict(submitted=len(trades),entered=sum(t['cost']>0 for t in trades),
        fully_entered=sum(t['cost']>0 and all(l['bought']==t['quantity'] for l in t['legs']) for t in trades),
        partial_entries=sum(t['cost']>0 and any(l['bought']<t['quantity'] for l in t['legs']) for t in trades),
        unpaired_entries=sum(len({l['bought'] for l in t['legs']})>1 for t in trades),
        closed=len(closed),event_blocks=len({t['event'] for t in closed}),entry_days=len({t['opened_at'][:10] for t in closed}),
        active=sum(t['status'] not in ['closed','cancelled'] for t in trades),
        realized=money(pnl),marked_net=money(sum(t['pnl'] for t in trades)),
        open_pnl=money(sum(t['pnl'] for t in trades if t['status'] not in ['closed','cancelled'])),
        deployed_outlay=money(sum(t['cost'] for t in trades)),completed_outlay=money(cost),
        completed_return=pnl/cost if cost else None,
        marked_return_on_all_outlay=sum(t['pnl'] for t in trades)/total if total else None,
        avg_win=statistics.mean(pos) if pos else None,avg_loss=statistics.mean(neg) if neg else None,
        profit_factor=sum(pos)/-sum(neg) if neg else None,expectancy=pnl/len(closed) if closed else None,
        win_rate=len(pos)/len(closed) if closed else None,win_ci=wilson(len(pos),len(closed)),
        fees=money(sum(t['fees'] for t in trades)),
        hold_mean=statistics.mean(holds) if holds else None,
        hold_median=statistics.median(holds) if holds else None,hold_p90=quantile(holds,.9) if holds else None,
        late_over_1h=len(lags),lag_max=max(lags,default=0),lag_median=statistics.median(lags) if lags else None,
        settlement_fallbacks=sum(any(f['mode']=='settlement' for f in t['fills']) for t in closed),
        before_expiry_exits=sum(not any(f['mode']=='settlement' for f in t['fills']) and ts(t['closed_at'])<min(ts(l.get('decision_time',l['close_time'])) for l in t['legs']) for t in closed),
        worst_pnl=min((t['pnl'] for t in closed),default=None),worst_return=min(ratios,default=None),
        worst_5pct_mean=statistics.mean(sorted(ratios)[:max(1,math.ceil(len(ratios)*.05))]) if ratios else None,
        effective_events=1/sum(w*w for w in weights) if weights else 0,
        largest_event_weight=max(weights,default=0),event_outlay=dict(groups))

def main():
    s=json.loads((ROOT/'data/state.json').read_text());cfg=s['config'];end=ts(s['updated_at']);start=end-14*86400
    trades=s['trades'];obs=[json.loads(line) for p in sorted((ROOT/'data/observations').glob('*.jsonl')) for line in p.read_text().splitlines()]
    prints={};missing=[];fees_bad=[];balances_bad=[];order_bad=[];cap_bad=[];count=0
    for o in obs:
        for h in o.get('trade_prints',[]):
            for p in h['prints'] or []: prints[(h['ticker'],p['trade_id'])]=p
    def check_fills(t,study=False):
        nonlocal count
        amounts=collections.defaultdict(int)
        if study:
            for l in t['legs']:amounts[(l['ticker'],l['side'])]=l['bought']
        prev=-1
        for f in t['fills']:
            at=ts(f['at']);key=(f['ticker'],f['side']);amounts[key]+=f['quantity']*(1 if f['action']=='buy' else -1)
            if at<prev or amounts[key]<0:order_bad.append(t['id'])
            prev=at
            l=next(l for l in t['legs'] if (l['ticker'],l['side'])==key)
            expected=0 if f['mode']=='settlement' else fee(f['price'],f['quantity'],f['mode'],l.get('fee_multiplier',1),l.get('maker_enabled',False))
            if abs(expected-f['fee'])>.00011:fees_bad.append(t['id'])
            if f.get('backfilled'):
                count+=1;p=prints.get((f['ticker'],f['source_trade_id']))
                if not p or abs(ts(p['created_time'])-at)>1e-6:missing.append(t['id'])
                if study and at<=ts(t['entry_completed_at']):order_bad.append(t['id'])
        for l in t['legs']:
            if amounts[(l['ticker'],l['side'])]!=l['bought']-l['sold']:balances_bad.append(t['id'])
    for t in trades:
        check_fills(t)
        debit=money(sum(f['quantity']*f['price']+f['fee'] for f in t['fills'] if f['action']=='buy'))
        credit=money(sum(f['quantity']*f['price']-f['fee'] for f in t['fills'] if f['action']=='sell'))
        if abs(t['cost']-debit)>.00011 or abs(t['proceeds']-credit)>.00011:balances_bad.append(t['id'])
        if debit>2000.00001 or t['cost_cap']>2000:cap_bad.append(t['id'])
    cash={}
    for k,b in s['buckets'].items():
        expected=money(b['initial']+b.get('net_transfers',0)+sum(t['proceeds']-t['cost'] for t in trades if t['bucket']==k))
        cash[k]=money(b['cash']-expected)
    study=s['profit_study'];split_bad=[];target_bad=[];recovery=[]
    profit_by_version={}
    for version in sorted({t['version'] for t in trades}):
        ids={t['id'] for t in trades if t['version']==version}
        subset=copy.deepcopy(study);subset['results']=[r for r in study['results'] if r['source_id'] in ids]
        profit_by_version[version]=analyze(subset,cfg)
    for c in study['cohorts']:
        source=next(t for t in trades if t['id']==c['source_id'])
        for v in c['variants']:
            if abs(sum(t['cost'] for t in v['slices'])-source['cost'])>.00011 or abs(sum(t['entry_fees'] for t in v['slices'])-sum(f['fee'] for f in source['fills'] if f['action']=='buy'))>.00011 or sum(t['quantity'] for t in v['slices'])!=source['quantity']:split_bad.append(c['source_id'])
            for t in v['slices']:
                check_fills(t,True)
                for l in t['legs']:
                    order=l.get('sell_order')
                    if order:
                        p=order['limit'];net=p*t['quantity']-fee(p,t['quantity'],'maker',l.get('fee_multiplier',1),l.get('maker_enabled',False))
                        if not 0<p<1 or net+1e-8<t['cost']+t['target_profit']:target_bad.append(t['id'])
    for policy in study['policies']:
        rows=[r for r in study['results'] if r['policy']==policy['id']]
        slices=[t for c in study['cohorts'] for v in c['variants'] if v['policy']==policy['id'] for t in v['slices']]
        recovery.append(dict(policy=policy['id'],tranches=len(slices),cash_target_recovered=sum(r['target_recovered_tranches'] for r in rows),
            limit_target_recovered=sum(bool(t.get('profit_recovered_at')) for t in slices),
            open_partial_exits=sum(any(l['sold']>0 for l in t['legs']) and any(l['bought']>l['sold'] for l in t['legs']) for t in slices),
            remaining_contracts=sum(l['bought']-l['sold'] for t in slices for l in t['legs']),
            open_mark=money(sum(r['mark'] for r in rows if not r['closed']))))
    hist=s['collection_history'];after8=[h for h in hist if h['at']>'2026-11-08T16:07:23Z'];gaps=[h['interval_hours'] for h in after8]
    runs=json.loads((ROOT/'data/research/2026-11-20-actions.json').read_text());scheduled=sorted([r for r in runs if r['event']=='schedule' and r['created_at']>'2026-11-08T16:07:23Z'],key=lambda r:r['created_at'])
    # Lower bound: delay from the most recent due cron slot. Actual originating slot is not exposed by run metadata.
    delays=[]
    for r in scheduled:
        d=datetime.fromtimestamp(ts(r['created_at']),timezone.utc);base=d.replace(hour=0,minute=7,second=0,microsecond=0).timestamp()
        slots=[base+3600*h for h in [-7,1,9,17]]
        delays.append((ts(r['created_at'])-max(v for v in slots if v<=ts(r['created_at'])))/3600)
    base=max((p for p in s['equity_history'] if ts(p['at'])<=start),key=lambda p:p['at'])
    recent_closed=[t for t in trades if t['status']=='closed' and ts(t['closed_at'])>=start]
    pairs=collections.defaultdict(dict)
    for t in trades:pairs[(t['version'],t['event'],t['created_at'])][t['mode']]=t
    paired=[]
    for (version,event,at),p in pairs.items():
        if len(p)==2 and all(t['status']=='closed' and t['cost']>0 for t in p.values()):
            paired.append(dict(version=version,event=event,horizon=p['maker']['horizon'],maker_return=p['maker']['pnl']/p['maker']['cost'],taker_return=p['taker']['pnl']/p['taker']['cost']))
    # Snapshot entry spread cost relative to contemporaneous midpoint; missing quotations are explicit.
    spread=0;matched=0;not_matched=0
    for t in trades:
        o=min(obs,key=lambda o:abs(ts(o['at'])-ts(t['created_at'])))
        for f in t['fills']:
            if f['action']!='buy' or f['mode']!='taker':continue
            book=o['books'].get(f['ticker'],{});bid,_=quote(book,f['side'],'sell') if book else (None,0);ask,_=quote(book,f['side'],'buy') if book else (None,0)
            if bid is not None and ask is not None and abs(ts(o['at'])-ts(f['at']))<60:
                spread+=(f['price']-(bid+ask)/2)*f['quantity'];matched+=1
            else:not_matched+=1
    buckets=[]
    for b in s['summary']['buckets']:
        st=stats([t for t in trades if t['bucket']==b['id']]);st.update(bucket=b['id'],allocated=b['allocated_capital'],return_on_current_allocation=b['net_pnl']/b['allocated_capital']);buckets.append(st)
    return dict(snapshot=s['updated_at'],trial_end=s['trial_end'],initial=cfg['initial_capital'],summary=s['summary'],
        inception=stats(trades),buckets=buckets,horizons={h:stats([t for t in trades if t['horizon']==h]) for h in cfg['horizons']},
        last14days=dict(requested_start=datetime.fromtimestamp(start,timezone.utc).isoformat(),baseline_at=base['at'],baseline_equity=base['equity'],observed_equity_change=money(s['summary']['equity']-base['equity']),closed_cohort=stats(recent_closed)),
        by_version={v:stats([t for t in trades if t['version']==v]) for v in sorted({t['version'] for t in trades})},
        diagnostics=diagnostics(s),profit_analysis=analyze(study,cfg),profit_recovery=recovery,exclusions=study['excluded'],
        profit_by_entry_version=profit_by_version,
        diagnostics_reproduced=diagnostics(s)==s['diagnostics'],profit_diagnostics_reproduced=analyze(study,cfg)==study['analysis'],
        audit=dict(cash_differences=cash,accounting_errors=balances_bad,fee_errors=fees_bad,sequence_errors=order_bad,cap_errors=cap_bad,max_entry_outlay=max(t['cost'] for t in trades),
            archived_observations=len(obs),archived_unique_prints=len(prints),backfilled_fills_checked=count,missing_prints=missing,split_errors=split_bad,target_errors=target_bad),
        cadence=dict(checks=len(hist),history_attempts=sum(h['attempted_trades'] for h in hist),history_failures=sum(h['failed_trades'] for h in hist),
            current_backfill=s['backfill'],profit_health=study['health'],checks_after_8h=len(after8),interval_median=statistics.median(gaps),interval_max=max(gaps),intervals_over_9h=sum(x>9 for x in gaps),
            scheduled_runs_after_8h=len(scheduled),delay_lower_bound_over_1h=sum(x>1 for x in delays),delay_lower_bound_median=statistics.median(delays),
            actions_conclusions=dict(collections.Counter(r['conclusion'] for r in runs)),archived_errors=[dict(at=o['at'],errors=o['errors']) for o in obs if o['errors']]),
        matched_execution_pairs=paired,
        spread_estimate=dict(taker_entry_midpoint_drag=money(spread),matched_buy_fills=matched,unmatched_buy_fills=not_matched,note='Half-spread plus entry depth cost versus observed midpoint, excludes exit spread; not causal slippage. Maker adverse selection and total round-trip spread cannot be isolated.'),
        stress=dict(all_open_marks_to_zero_equity=money(s['summary']['equity']-sum(t['mark'] for t in trades if t['status'] not in ['closed','cancelled'])),double_realized_fees_equity=money(s['summary']['equity']-s['summary']['fees'])),
        covariance_note='15 calendar entry dates span only 14 elapsed days: insufficient for stable covariance or day-cluster confidence intervals. BTC daily events share common price dynamics; CPI repeats are one event. Event blocks are identifiers, not proof of independence. No covariance-based allocation, annualized ratios or Kelly.')

if __name__=='__main__':print(json.dumps(main(),indent=2,allow_nan=False))
