"""Predeclared paired exit comparisons; no optimized sizing or fitted forecasts."""
import collections
import math
import random
from engine import money
from review_stats import quantile


def wilson(wins, n):
    if not n:
        return None
    z=1.96; p=wins/n; den=1+z*z/n
    mid=(p+z*z/(2*n))/den; rad=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,mid-rad),min(1,mid+rad)]


def holm(pvalues):
    """Holm adjustment within one prespecified set of paired policy comparisons."""
    ordered=sorted(pvalues.items(),key=lambda x:x[1]); adjusted={}; previous=0
    for i,(key,p) in enumerate(ordered):
        previous=max(previous,min(1,p*(len(ordered)-i))); adjusted[key]=previous
    return adjusted


def paired_bootstrap(rows, baseline, minimum=20, draws=2000):
    groups=collections.defaultdict(lambda:[0,0,0])
    for r in rows:
        reference=baseline[r['source_id']]
        g=groups[r['event']]
        g[0]+=r['cost']; g[1]+=r['net_pnl']; g[2]+=r['net_pnl']-reference['net_pnl']
    blocks=list(groups.values())
    if len(blocks)<minimum:
        return {'return_ci':None,'delta_ci':None,'p_raw':None}
    rng=random.Random(20261108); returns=[]; differences=[]; extreme=0
    observed=sum(g[2] for g in blocks)
    for _ in range(draws):
        sample=rng.choices(blocks,k=len(blocks)); denominator=sum(g[0] for g in sample)
        returns.append(sum(g[1] for g in sample)/denominator)
        differences.append(sum(g[2] for g in sample)/denominator)
        # Paired cluster sign-flip null. Exchangeable symmetric event effects assumed.
        flipped=sum(g[2]*rng.choice([-1,1]) for g in blocks)
        extreme+=abs(flipped)>=abs(observed)-1e-12
    return {'return_ci':[quantile(returns,.025),quantile(returns,.975)],
            'delta_ci':[quantile(differences,.025),quantile(differences,.975)],
            'p_raw':(extreme+1)/(draws+1)}


def analyze(study, cfg):
    spec=cfg['profit_study']; results=study['results']; reference=spec['reference_policy']
    complete=set(study['matched_complete_ids']); output=[]
    # Pool only descriptively; stratification makes horizon/execution confounding visible.
    for horizon in ['all',*cfg['horizons']]:
        for mode in ['all','maker','taker']:
            selected=[r for r in results if (horizon=='all' or r['horizon']==horizon) and (mode=='all' or r['entry_mode']==mode)]
            selected_complete=[r for r in selected if r['source_id'] in complete]
            ref={r['source_id']:r for r in selected_complete if r['policy']==reference}
            rows=[]; pvalues={}
            # All-position marks are reported only for sources priced in EVERY policy.
            unavailable={r['source_id'] for r in selected if r['valuation_incomplete'] or r['history_incomplete']}
            for policy in study['policies']:
                all_rows=[r for r in selected if r['policy']==policy['id']]
                trades=[r for r in selected_complete if r['policy']==policy['id']]
                marked=[r for r in all_rows if r['source_id'] not in unavailable]
                n=len(trades); cost=sum(r['cost'] for r in trades); pnl=sum(r['net_pnl'] for r in trades)
                ratios=[r['net_pnl']/r['cost'] for r in trades]; durations=[r['hold_hours'] for r in trades]
                wins=sum(r['net_pnl']>0 for r in trades); gains=sum(max(0,r['net_pnl']) for r in trades); losses=-sum(min(0,r['net_pnl']) for r in trades)
                positive=[r['net_pnl'] for r in trades if r['net_pnl']>0]; negative=[r['net_pnl'] for r in trades if r['net_pnl']<0]
                events=len({r['event'] for r in trades}); fees=sum(r['fees'] for r in trades)
                tail=sorted(ratios)[:max(1,math.ceil(n*.05))]
                concentration=collections.defaultdict(float)
                for r in trades: concentration[r['event']]+=r['cost']
                weights=[value/cost for value in concentration.values()] if cost else []
                stats=paired_bootstrap(trades,ref,spec['minimum_event_blocks'],spec['bootstrap_draws'])
                if policy['id']!=reference and stats['p_raw'] is not None: pvalues[policy['id']]=stats['p_raw']
                rows.append({'policy':policy['id'],'label':policy['label'],'assigned':len(all_rows),
                    'closed':sum(r['closed'] for r in all_rows),'matched_closed':n,'event_blocks':events,
                    'net_pnl':money(pnl) if n else None,'net_return':pnl/cost if cost else None,
                    'expectancy':pnl/n if n else None,'median_return':quantile(ratios,.5) if n else None,
                    'average_win':sum(positive)/len(positive) if positive else None,
                    'average_loss':sum(negative)/len(negative) if negative else None,
                    'win_rate':wins/n if n else None,'win_ci':wilson(wins,n),
                    'profit_factor':gains/losses if losses else None,'no_losses_yet':bool(n and not losses),
                    'fees':money(fees) if n else None,'fee_drag':fees/cost if cost else None,
                    'average_hold_hours':sum(durations)/n if n else None,
                    'median_hold_hours':quantile(durations,.5) if n else None,
                    'p90_hold_hours':quantile(durations,.9) if n else None,
                    'early_exit_rate':sum(r['early_exit'] for r in trades)/n if n else None,
                    'settlement_fallbacks':sum(r['settlement_fallback'] for r in all_rows),
                    'late_exits':sum(r['late_exit'] for r in all_rows),
                    'unreachable':sum(r['unreachable'] for r in all_rows),
                    'unresolved':sum(not r['closed'] for r in all_rows),
                    'matched_marked':len(marked),'marked_net_pnl':money(sum(r['net_pnl'] for r in marked)) if marked else None,
                    'unpriced_or_incomplete_sources':len(unavailable),
                    'worst_return':min(ratios) if n else None,
                    'expected_shortfall_05':sum(tail)/len(tail) if events>=20 else None,
                    'effective_event_count':1/sum(w*w for w in weights) if weights else 0,
                    'largest_event_weight':max(weights) if weights else None,
                    'double_fee_return':(pnl-fees)/cost if cost else None,
                    'one_cent_exit_slippage_return':(pnl-.01*sum(r['nonsettlement_sold'] for r in trades))/cost if cost else None,
                    'delta_vs_25':sum(r['net_pnl']-ref[r['source_id']]['net_pnl'] for r in trades)/cost if cost else None,
                    **stats})
            adjusted=holm(pvalues)
            for row in rows: row['p_holm']=adjusted.get(row['policy'])
            output.append({'horizon':horizon,'entry_mode':mode,'rows':rows})
    return {'strata':output,'reference_policy':reference,'minimum_event_blocks':spec['minimum_event_blocks'],
        'method':'Same entry, quantity, fees, loss trigger and time cutoff across five exit policies. '
                 'Return = sum(net P&L) / sum(entry outlay). Only entries closed in every policy enter completed comparisons; '
                 'all-policy marks and unresolved counts remain separate to expose completion bias. '
                 '2,000 paired event-cluster bootstrap draws provide percentile 95% return/difference intervals after 20 events. '
                 'Cluster sign-flip p-values assume symmetric exchangeable event effects; Holm adjusts four comparisons against +25% within each stratum. '
                 'Intervals are not simultaneous; repeated reviews, cross-event correlation and the 12 strata remain exploratory. '
                 'Wilson win intervals treat trades as independent and are descriptive. Effective event count = 1 / sum(event outlay weights squared). '
                 'Worst-5% mean is empirical, not a tail forecast. Stress tests are accounting haircuts, not execution replays. '
                 'No annualized Sharpe, Kelly sizing or promotion based only on a p-value. Freeze hypotheses now; any future tuning requires a new prospective validation period.'}
