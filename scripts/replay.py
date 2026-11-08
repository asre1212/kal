"""Chronological paper fills for resting orders, never inferred from candle extremes."""
import math
from engine import fee,iso,ts,money,ledger_fill,finalize

def cutoff(state,t,cfg):
    return min(ts(t['opened_at'])+cfg['horizons'][t['horizon']]['max_hold_hours']*3600,
               min(ts(l['close_time']) for l in t['legs'])-1800,ts(state['trial_end']))

def arm_exits(state,t,cfg,at):
    """Contingent limits defined by the existing target formula, activated after entry."""
    if t['mode']!='maker' or t['status']!='open':return
    maximum=max(0,(t['quantity']-t['cost'])/max(t['cost'],.0001))
    # Explicit counterfactual targets are never silently clipped to another tier.
    t['target_ratio']=t.get('exit_target_ratio',min(cfg['profit_target'],maximum*.5))
    t['target_profit']=money(t['cost']*t['target_ratio'])
    if at>=cutoff(state,t,cfg):return
    for l in t['legs']:
        if l.get('sell_order') or l['bought']==l['sold']:continue
        # Choose the smallest cent limit that recovers the basket's target after this leg's fee.
        target=t['cost']+t['target_profit']
        price=next((i/100 for i in range(1,100) if i/100*t['quantity']-fee(i/100,t['quantity'],'maker',l.get('fee_multiplier',1),l.get('maker_enabled',False))>=target),None)
        if price is not None:l['sell_order']={'action':'sell','side':l['side'],'limit':price,'after':iso(at),'activated_at':iso(at),'participation':cfg['participation'],'filled':0,'eligible_volume':0,'model':'touch-volume-v2'}
        elif 'exit_target_ratio' in t:l['target_unreachable']=True

def replay_trade(state,t,history,now,cfg,volume_used):
    """History must be complete for every leg. Recorded orders only; no retrospective entries."""
    start=ts(t.get('replay_after',state['updated_at']))
    events={}
    for l in t['legs']:
        for p in history[l['ticker']]:
            at=ts(p['created_time'])
            if start<at<=now and not p.get('is_block_trade'):
                events[(l['ticker'],p['trade_id'])]=(at,l,p)
    stop=cutoff(state,t,cfg)
    before=len(t['fills']);target_reached=False
    for at,l,p in sorted(events.values(),key=lambda e:(e[0],e[1]['ticker'],e[2]['trade_id'])):
        if t['status'] in ['closed','cancelled','closing'] or at>=stop:break
        if t['status']=='entering':
            if at>ts(t['entry_deadline']):break
            action='buy';order=l.get('buy_order');remaining=t['quantity']-l['bought']
        else:
            action='sell';order=l.get('sell_order');remaining=l['bought']-l['sold']
        if not order or remaining<=0 or at<=ts(order['after']):continue
        aggressor=p.get('taker_side') or p.get('taker_outcome_side')
        if aggressor not in ['yes','no']:continue
        price=float(p[l['side']+'_price_dollars'])
        eligible=(action=='buy' and aggressor!=l['side'] and price<=order['limit']+1e-8) or (action=='sell' and aggressor==l['side'] and price>=order['limit']-1e-8)
        if not eligible:continue
        # Each public print can support at most the participation share across all paper orders.
        key=(l['ticker'],p['trade_id']);available=max(0,float(p['count_fp'])*cfg['participation']-volume_used.get(key,0))
        # Reserve only this order's remaining need, allowing another tranche/order
        # to use the rest of the same print without exceeding the shared 5% cap.
        allocated=min(available,max(0,remaining-order.get('credit',0)))
        order['credit']=order.get('credit',0)+allocated;volume_used[key]=volume_used.get(key,0)+allocated
        q=min(remaining,math.floor(order['credit']+1e-9))
        if action=='buy':
            while q and (t['cost']+order['limit']*q+fee(order['limit'],q,'maker',l.get('fee_multiplier',1),l.get('maker_enabled',False))>t['cost_cap']+1e-6 or order['limit']*q+fee(order['limit'],q,'maker',l.get('fee_multiplier',1),l.get('maker_enabled',False))>state['buckets'][t['bucket']]['cash']):q-=1
        if not q:continue
        order['credit']-=q
        ledger_fill(state,t,l,action,order['limit'],q,'maker',at,'Backfilled limit estimate: timestamped trade at/through limit; 5% volume, queue unknown')
        t['fills'][-1].update({'recorded_at':iso(now),'source_trade_id':p['trade_id'],'backfilled':True,'execution_model':'touch-volume-v2'})
        if action=='buy':
            f=t['fills'][-1];t['reserved']=money(max(0,t['reserved']-f['price']*q-f['fee']))
            if all(x['bought']==t['quantity'] for x in t['legs']):
                t['status']='open';t['reserved']=0;t['entry_completed_at']=iso(at);arm_exits(state,t,cfg,at)
        else:
            if t['proceeds']>=t['cost']+t.get('target_profit',0):target_reached=True
            if all(x['sold']==x['bought'] for x in t['legs']):
                t['exit_reason']='Backfilled limit exits';finalize(t,at)
    # Never fabricate a historical market sale for a residual leg.
    if target_reached and t['status']=='open':t['profit_recovered_at']=next(f['at'] for f in reversed(t['fills']) if f['action']=='sell')
    t['replay_after']=iso(now)
    for l in t['legs']:
        for name in ['buy_order','sell_order']:
            if l.get(name):l[name]['after']=iso(now)
    return len(t['fills'])-before
