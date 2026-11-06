"""Review diagnostics: no forecast or optimized sizing is inferred from sparse samples."""
import collections,math,random

def quantile(xs,p):
    xs=sorted(xs);i=(len(xs)-1)*p;lo=int(i);return xs[lo]+(xs[min(lo+1,len(xs)-1)]-xs[lo])*(i-lo)

def diagnostics(state):
    results=[]
    for key in state['buckets']:
        for version in sorted({t['version'] for t in state['trades'] if t['bucket']==key} or {'baseline-v1'}):
            trades=[t for t in state['trades'] if t['bucket']==key and t['version']==version and t['status']=='closed' and t['cost']>0]
            # Repeated bets on the same event remain one block, including different entry days.
            groups=collections.defaultdict(list)
            for t in trades:groups[t['event']].append(t)
            rows=list(groups.values());n=len(rows);cost=sum(t['cost'] for t in trades);pnl=sum(t['pnl'] for t in trades)
            wins=sum(max(t['pnl'],0) for t in trades);loss=-sum(min(t['pnl'],0) for t in trades)
            ratios=[t['pnl']/t['cost'] for t in trades];ci=None
            if n>=20:
                rng=random.Random(20261106);draws=[]
                for _ in range(2000):
                    sample=[t for row in rng.choices(rows,k=n) for t in row]
                    draws.append(sum(t['pnl'] for t in sample)/sum(t['cost'] for t in sample))
                ci=[quantile(draws,.025),quantile(draws,.975)]
            tail=sorted(ratios)[:max(1,math.ceil(len(ratios)*.05))]
            results.append({'bucket':key,'version':version,'closed':len(trades),'event_blocks':n,'net_return_on_outlay':pnl/cost if cost else None,'return_ci':ci,'profit_factor':wins/loss if loss else None,'worst_tail_mean':sum(tail)/len(tail) if len(ratios)>=20 else None,'evidence':'exploratory' if ci else 'insufficient independent events'})
    return {'method':'2,000 event-cluster bootstrap resamples; 95% percentile interval; minimum 20 event blocks. Not adjusted for cross-event correlation or multiple comparisons. Worst-tail mean uses lowest 5% of trade returns and is descriptive, not a forecast. No annualized Sharpe for this short trial.','rows':results}

if __name__=='__main__':
    import json
    from pathlib import Path
    print(json.dumps(diagnostics(json.loads((Path(__file__).resolve().parents[1]/'data/state.json').read_text())),indent=2))
