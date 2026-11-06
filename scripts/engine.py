"""Deterministic paper ledger. No authenticated trading endpoints or real orders."""
from decimal import Decimal, ROUND_CEILING
from datetime import datetime, timezone
import math

D = lambda n: Decimal(str(n))
def money(n): return float(D(n).quantize(D('0.0001')))
def ts(s): return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def iso(n): return datetime.fromtimestamp(n,timezone.utc).isoformat().replace('+00:00','Z')
def fee(p,q,mode='taker',multiplier=1,maker_enabled=False):
    k = D('.0175') if mode=='maker' and maker_enabled else D('0') if mode=='maker' else D('.07')
    return float((k*D(multiplier)*D(q)*D(p)*(1-D(p))).quantize(D('.0001'),rounding=ROUND_CEILING))
def levels(book,side,action):
    if action=='buy': return sorted([(money(1-p),q) for p,q in book['no' if side=='yes' else 'yes']],key=lambda x:x[0])
    return sorted(book[side],reverse=True)
def quote(book,side,action):
    rows=levels(book,side,action)
    return tuple(rows[0]) if rows else (None,0)
def execution(book,side,action,quantity,limit=None,participation=1):
    fills=[]; remaining=quantity
    for price,size in levels(book,side,action):
        if limit is not None and ((action=='buy' and price>limit+1e-8) or (action=='sell' and price<limit-1e-8)): break
        n=min(remaining,math.floor(size*participation))
        if n>0: fills.append((price,n)); remaining-=n
        if not remaining: break
    return fills

def initial_state(now,config):
    end=now+config['trial_days']*86400
    buckets={f'{h}_{mode}':{'horizon':h,'mode':mode,'initial':config['bucket_capital'],'cash':config['bucket_capital']} for h in config['horizons'] for mode in ['maker','taker']}
    return {'schema':1,'started_at':iso(now),'trial_end':iso(end),'updated_at':iso(now),'buckets':buckets,'trades':[],'equity_history':[{'at':iso(now),'equity':config['initial_capital'],'cash':config['initial_capital'],'fees':0,'drawdown':0,'buckets':{k:config['bucket_capital'] for k in buckets}}], 'runs':0,'reviews':[],'changes':[], 'health':{}, 'candidates':[]}

def ledger_fill(state,trade,leg,action,p,q,mode,at,reason):
    if q<=0: return
    f=0 if mode=='settlement' else fee(p,q,mode,leg.get('fee_multiplier',1),leg.get('maker_enabled',False))
    cash=state['buckets'][trade['bucket']]
    if action=='buy':
        debit=money(p*q+f)
        if trade['cost']+debit>trade['cost_cap']+1e-6: raise ValueError('Trade cap exceeded')
        if cash['cash']<debit-1e-6: raise ValueError('Negative cash forbidden')
        cash['cash']=money(cash['cash']-debit); trade['cost']=money(trade['cost']+debit); leg['bought']+=q
    else:
        if q>leg['bought']-leg['sold']: raise ValueError('Cannot sell unowned contracts')
        cash['cash']=money(cash['cash']+p*q-f); trade['proceeds']=money(trade['proceeds']+p*q-f); leg['sold']+=q
    trade['fees']=money(trade['fees']+f)
    trade['fills'].append({'at':iso(at),'ticker':leg['ticker'],'side':leg['side'],'action':action,'price':p,'quantity':q,'fee':f,'mode':mode,'reason':reason})

def mark_trade(trade,books):
    value=0; missing=False; depth_shortfall=False
    for leg in trade['legs']:
        q=leg['bought']-leg['sold']
        if not q:continue
        b=books.get(leg['ticker'])
        if not b:
            missing=True
            # Zero for unpriced contracts prevents stale marks from overstating equity.
            leg['mark']=0;leg['unpriced']=True;continue
        fs=execution(b,leg['side'],'sell',q)
        leg['mark']=money(sum(p*n-fee(p,n,'taker',leg.get('fee_multiplier',1)) for p,n in fs))
        leg['unpriced']=False
        if sum(n for _,n in fs)<q:depth_shortfall=True
        value+=leg['mark']
    trade['mark']=money(value);trade['pnl']=money(trade['proceeds']+value-trade['cost'])
    trade['unpriced']=missing;trade['depth_shortfall']=depth_shortfall
    return value

def close_at_book(state,t,books,now,reason,participation=1):
    for l in t['legs']:
        q=l['bought']-l['sold']; b=books.get(l['ticker'])
        if q and b:
            for p,n in execution(b,l['side'],'sell',q,participation=participation):ledger_fill(state,t,l,'sell',p,n,'taker',now,reason)
    t['exit_reason']=reason;t['status']='closing'

def finalize(t,now):
    if t['status'] in ['closing','open'] and all(l['bought']==l['sold'] for l in t['legs']):
        t['status']='closed';t['closed_at']=iso(now);t['pnl']=money(t['proceeds']-t['cost']);t['mark']=0
        t['hold_hours']=round((now-ts(t['opened_at']))/3600,2)

def observed_maker_fills(order,prints,quantity):
    """Strict trade-through only, 5% volume; queue not known. Returns conservative estimate."""
    eligible=0
    for p in prints:
        if ts(p['created_time'])<=ts(order['after']):continue
        if p.get('is_block_trade'):continue
        side=order['side']; price=float(p[f'{side}_price_dollars']); aggressor=p.get('taker_side') or p.get('taker_outcome_side')
        if order['action']=='buy' and aggressor!=side and price<order['limit']-0.00001: eligible+=float(p['count_fp'])
        if order['action']=='sell' and aggressor==side and price>order['limit']+0.00001: eligible+=float(p['count_fp'])
    return min(quantity,math.floor(eligible*order.get('participation',.05)))

def summarize(state,config,books,now):
    stats=[]; total=config['reserve'];cash=config['reserve'];fees=0
    for key,b in state['buckets'].items():
        trades=[t for t in state['trades'] if t['bucket']==key]; value=sum(mark_trade(t,books) for t in trades if t['status'] not in ['closed','cancelled'])
        eq=money(b['cash']+value);total+=eq;cash+=b['cash'];fs=sum(t['fees'] for t in trades);fees+=fs
        closed=[t for t in trades if t['status']=='closed']; wins=sum(t['pnl']>0 for t in closed);n=len(closed)
        ci=None
        if n:
            z=1.96;p=wins/n;den=1+z*z/n;mid=(p+z*z/(2*n))/den;rad=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den;ci=[mid-rad,mid+rad]
        stats.append({'id':key,**b,'equity':eq,'net_pnl':money(eq-b['initial']),'realized':money(sum(t['pnl'] for t in closed)),'fees':money(fs),'closed':n,'wins':wins,'win_rate':wins/n if n else None,'win_ci':ci,'expectancy':money(sum(t['pnl'] for t in closed)/n) if n else None,'open':sum(t['status'] in ['open','closing','entering'] for t in trades),'submitted':len(trades),'entered':sum(t['cost']>0 for t in trades),'avg_hold':sum(t.get('hold_hours',0) for t in closed)/n if n else None})
    incomplete=any(t.get('unpriced') or t.get('depth_shortfall') for t in state['trades'] if t['status'] not in ['closed','cancelled'])
    peak=max([config['initial_capital'],total]+[x['equity'] for x in state['equity_history'] if not x.get('valuation_incomplete')]);dd=(peak-total)/peak if not incomplete else None
    point={'at':iso(now),'equity':money(total),'cash':money(cash),'fees':money(fees),'drawdown':dd,'valuation_incomplete':incomplete,'buckets':{b['id']:b['equity'] for b in stats}}
    state['equity_history'].append(point)
    state['summary']={'equity':money(total),'cash':money(cash),'net_pnl':money(total-config['initial_capital']),'fees':money(fees),'drawdown':dd,'valuation_incomplete':incomplete,'max_drawdown':max([0]+[x['drawdown'] for x in state['equity_history'] if x.get('drawdown') is not None]),'closed':sum(x['closed'] for x in stats),'open':sum(x['open'] for x in stats),'buckets':stats,'paused_for_risk':dd is not None and dd>=config['max_drawdown']}
    return state['summary']
