#!/usr/bin/env python3
"""Public GET-only collector and forward simulator; run once per scheduled check."""
import json,os,time,math,urllib.request,urllib.parse,urllib.error,collections,concurrent.futures,threading
from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from engine import *
from review_stats import diagnostics
from replay import replay_trade,arm_exits,cutoff
ROOT=Path(__file__).resolve().parents[1]
BASE='https://external-api.kalshi.com/trade-api/v2'
FIELDS=['ticker','event_ticker','title','yes_sub_title','close_time','expected_expiration_time','expiration_time','floor_strike','cap_strike','strike_type','yes_bid_dollars','yes_ask_dollars','no_bid_dollars','no_ask_dollars','volume_fp','result','settlement_value_dollars','status','rules_primary','rules_secondary','open_time']
ERRORS=[]
REQUEST_LOCK=threading.Lock()
NEXT_REQUEST=0.0
def get(path,**params):
    global NEXT_REQUEST
    url=BASE+path+('?' + urllib.parse.urlencode(params) if params else '')
    for attempt in range(5):
        try:
            # Pace all worker threads together; pagination must not burst through API limits.
            with REQUEST_LOCK:
                delay=max(0,NEXT_REQUEST-time.monotonic())
                if delay:time.sleep(delay)
                NEXT_REQUEST=time.monotonic()+.25
            req=urllib.request.Request(url,headers={'User-Agent':'KalPaperResearch/1.0','Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=18) as r:return json.load(r)
        except Exception as e:
            if attempt==4:raise RuntimeError(f'{path}: {e}')
            delay=min(16,2**(attempt+1))
            if isinstance(e,urllib.error.HTTPError) and e.code==429:
                try:delay=max(delay,min(20,float(e.headers.get('Retry-After',0))))
                except (TypeError,ValueError):pass
            time.sleep(delay)
def safe(fn,*args):
    try:return fn(*args)
    except Exception as e:ERRORS.append(str(e));return None

def series_markets(series):
    meta=get('/series/'+series)['series']; markets=[]; cursor=''
    for _ in range(4):
        d=get('/markets',series_ticker=series,status='open',limit=1000,**({'cursor':cursor} if cursor else {}))
        for m in d.get('markets',[]):
            m={k:m.get(k) for k in FIELDS};m['series']=series
            m['fee_multiplier']=float(meta.get('fee_multiplier',1));m['fee_type']=meta.get('fee_type','unknown')
            m['maker_enabled']=m['fee_type']=='quadratic_with_maker_fees'
            markets.append(m)
        cursor=d.get('cursor')
        if not cursor:break
    return markets

def decision_time(m):
    # For known time-ladder markets close is the explicit deadline, not a broad election fallback.
    return ts(m['close_time'] if m.get('series')=='KXIMPEACH' or m['ticker'].startswith('KXIMPEACH-') else (m.get('expected_expiration_time') or m['close_time']))
def horizon(m,now,cfg):
    days=(decision_time(m)-now)/86400
    for k,h in cfg['horizons'].items():
        if h['min_days']<=days<=h['max_days']:
            if k=='day' and datetime.fromtimestamp(decision_time(m),ZoneInfo('America/New_York')).date()!=datetime.fromtimestamp(now,ZoneInfo('America/New_York')).date():continue
            return k
    return None

def valid(m,cfg):
    try:
        a=float(m['yes_ask_dollars']);b=float(m['yes_bid_dollars'])
        return m['fee_type'] in ['quadratic','quadratic_with_maker_fees'] and .005<=b<=a<=.995 and a-b<=cfg['max_spread'] and float(m.get('volume_fp') or 0)>=cfg['min_volume']
    except (TypeError,ValueError):return False

def candidates(markets,now,cfg):
    groups=collections.defaultdict(list);out=[]
    for m in markets:
        if horizon(m,now,cfg) and valid(m,cfg):groups[(m['event_ticker'],horizon(m,now,cfg))].append(m)
    def add(h,kind,legs,event):
        cost=sum(float(m[f'{side}_ask_dollars']) for m,side in legs)
        if not .08<=cost<=.92:return
        if len({m['ticker'] for m,_ in legs})!=len(legs):return
        if min(decision_time(m) for m,_ in legs)-now<3600:return
        out.append({'horizon':h,'kind':kind,'event':event,'legs':legs,'indicative_cost':cost,'score':sum(math.log1p(float(m.get('volume_fp') or 0)) for m,_ in legs)-abs(cost-.45)*4})
    for (event,h),ms in groups.items():
        thresholds=sorted([m for m in ms if m['strike_type']=='greater' and m['floor_strike'] is not None],key=lambda m:m['floor_strike'])
        # Same event AND same observation time: only curated numeric threshold series.
        if ms[0]['series']!='KXIMPEACH':
            for i,lo in enumerate(thresholds):
                for hi in thresholds[i+1:]:
                    if abs(decision_time(hi)-decision_time(lo))>60:continue
                    if float(lo['yes_bid_dollars'])>=.6 and float(hi['yes_ask_dollars'])<=.4:add(h,'Threshold strangle',[(lo,'no'),(hi,'yes')],event)
        ranges=[m for m in ms if m['strike_type']=='between']
        if ranges:
            best=max(ranges,key=lambda m:float(m['yes_bid_dollars']))
            if .5<=float(best['yes_bid_dollars'])<=.9:add(h,'Outside central range',[(best,'no')],event)
        if ms[0]['series']=='KXIMPEACH':
            ordered=sorted(ms,key=decision_time)
            for i,early in enumerate(ordered):
                for late in ordered[i+1:]:
                    if decision_time(late)-decision_time(early)>20*86400:
                        add(h,'Outside timing window',[(early,'yes'),(late,'no')],event)
    # One fixed opportunity per event/horizon. No forced entry when liquidity filters fail.
    picks={}
    for c in sorted(out,key=lambda c:c['score'],reverse=True):picks.setdefault((c['event'],c['horizon']),c)
    return list(picks.values())

def readbook(ticker):
    d=get('/markets/'+ticker+'/orderbook',depth=30)['orderbook_fp']
    return {side:[[float(p),float(q)] for p,q in d.get(side+'_dollars',[])] for side in ['yes','no']}

def prints_since(ticker,since,now):
    # Complete pagination or fail closed. Returning partial history would bias the backfill.
    cursor='';out={};seen=set()
    for _ in range(200):
        d=get('/markets/trades',ticker=ticker,min_ts=math.floor(since),max_ts=math.ceil(now),limit=1000,is_block_trade='false',**({'cursor':cursor} if cursor else {}))
        for p in d.get('trades',[]):
            for field in ['trade_id','created_time','yes_price_dollars','no_price_dollars','count_fp']:
                if p.get(field) is None:raise RuntimeError('Incomplete print '+field+' for '+ticker)
            if (p.get('taker_side') or p.get('taker_outcome_side')) not in ['yes','no']:raise RuntimeError('Missing aggressor side for '+ticker)
            if since<ts(p['created_time'])<=now and not p.get('is_block_trade'):out[p['trade_id']]=p
        cursor=d.get('cursor')
        if not cursor:return sorted(out.values(),key=lambda p:(ts(p['created_time']),p['trade_id']))
        if cursor in seen:raise RuntimeError('Repeated history cursor for '+ticker)
        seen.add(cursor)
    raise RuntimeError('History exceeds 200 pages; leave orders unresolved for '+ticker)

def enter(state,c,mode,books,now,cfg):
    bucket=f"{c['horizon']}_{mode}";legs=[]
    if bucket in cfg['paused_buckets']:return
    existing=[t for t in state['trades'] if t['bucket']==bucket]
    # Limit new exposure to one basket per event and mode each day, never average down.
    today=iso(now)[:10]
    if any(t['event']==c['event'] and (t['status'] not in ['closed','cancelled'] or t['created_at'][:10]==today) for t in existing):return
    exposure=sum(t['cost']-t['proceeds']+t.get('reserved',0) for t in existing if t['status'] not in ['closed','cancelled'])
    event_exposure=sum(max(0,t['cost']-t['proceeds'])+t.get('reserved',0) for t in state['trades'] if t['event']==c['event'] and t['status'] not in ['closed','cancelled'])
    reserved=sum(t.get('reserved',0) for t in existing)
    budget=min(cfg['max_trade_cost'],cfg['max_bucket_exposure']-exposure,cfg['max_event_exposure']-event_exposure,state['buckets'][bucket]['cash']-reserved)
    if budget<5:return
    available=[];unit=0
    for m,side in c['legs']:
        b=books.get(m['ticker'])
        if not b:return
        ask,asksize=quote(b,side,'buy');bid,bidsize=quote(b,side,'sell')
        if ask is None or bid is None or ask-bid>cfg['max_spread'] or ask>=.99 or bid<.01:return
        price=ask if mode=='taker' else bid
        unit+=price+fee(price,1,mode,m['fee_multiplier'],m['maker_enabled'])
        available.append(math.floor(asksize*cfg['participation']))
        legs.append({'ticker':m['ticker'],'title':m['title'],'subtitle':m['yes_sub_title'],'side':side,'limit':price,'bought':0,'sold':0,'fee_multiplier':m['fee_multiplier'],'maker_enabled':m['maker_enabled'],'close_time':m['close_time'],'decision_time':iso(decision_time(m)),'rules':m['rules_primary']})
    qty=min(math.floor(budget/unit),min(available),1000)
    if qty<1:return
    # Recalculate exact total fees, reduce if any rounding could exceed cap.
    while qty and sum(l['limit']*qty+fee(l['limit'],qty,mode,l['fee_multiplier'],l['maker_enabled']) for l in legs)>budget:qty-=1
    if not qty:return
    cap=money(sum(l['limit']*qty+fee(l['limit'],qty,mode,l['fee_multiplier'],l['maker_enabled']) for l in legs))
    t={'id':f"T{len(state['trades'])+1:05d}",'bucket':bucket,'horizon':c['horizon'],'mode':mode,'strategy':c['kind'],'event':c['event'],'version':cfg['version'],'created_at':iso(now),'opened_at':iso(now),'status':'entering','quantity':qty,'cost':0,'proceeds':0,'fees':0,'cost_cap':min(budget,cfg['max_trade_cost']),'reserved':cap,'legs':legs,'fills':[],'pnl':0,'mark':0,'entry_deadline':iso(now+(3600 if c['horizon']=='day' else 86400)),'target_ratio':cfg['profit_target'],'reason':'Equal-count tails; prices and spreads passed fixed baseline filters. Research trade, no forecast edge asserted.'}
    t['replay_after']=iso(now);t['execution_model']=cfg.get('execution_model','touch-volume-v2') if mode=='maker' else 'book-snapshot-v1'
    state['trades'].append(t)
    if mode=='taker':
        for l in legs:ledger_fill(state,t,l,'buy',l['limit'],qty,'taker',now,'Entry at observed ask; capped at 5% displayed top-level size')
        t['reserved']=0;t['status']='open'
    else:
        for l in legs:l['buy_order']={'action':'buy','side':l['side'],'limit':l['limit'],'after':iso(now),'participation':cfg['participation']}

def process(state,books,markets,now,cfg):
    md={m['ticker']:m for m in markets};requests={};history={};prints={};volume_used={}
    active=[t for t in state['trades'] if t['status'] not in ['closed','cancelled']]
    stats={'at':iso(now),'interval_hours':(now-ts(state['updated_at']))/3600,'attempted_trades':0,'complete_trades':0,'failed_trades':0,'backfilled_fills':0,'late_exit_trades':[],'max_exit_lag_hours':0}
    for t in active:
        if t['mode']=='taker':t['execution_model']='book-snapshot-v1'
        t.setdefault('replay_after',state['updated_at'])
        if t['mode']=='maker':
            for l in t['legs']:requests[l['ticker']]=min(requests.get(l['ticker'],now),ts(t['replay_after']))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for ticker,rows in zip(requests,pool.map(lambda ticker:safe(prints_since,ticker,requests[ticker],now),requests)):
            history[ticker]=rows;prints[(ticker,iso(requests[ticker]))]=rows
    for t in active:
        if t['mode']=='maker':
            stats['attempted_trades']+=1
            if any(history.get(l['ticker']) is None for l in t['legs']):
                stats['failed_trades']+=1;t['backfill_status']='incomplete: checkpoint retained';continue
            stats['complete_trades']+=1
            stats['backfilled_fills']+=replay_trade(state,t,history,now,cfg,volume_used)
            t['backfill_status']='complete';t['execution_model']=cfg.get('execution_model','touch-volume-v2')
            if t['status'] in ['closed','cancelled']:continue
        deadline=ts(t['entry_deadline']) if t['status']=='entering' else cutoff(state,t,cfg)
        if now>deadline and any(l['bought']>l['sold'] for l in t['legs']):
            lag=(now-deadline)/3600;t['time_exit_lag_hours']=round(lag,3)
            stats['late_exit_trades'].append(t['id']);stats['max_exit_lag_hours']=max(stats['max_exit_lag_hours'],lag)
        # Historical limits are resolved before today's final settlement, never the reverse.
        for l in t['legs']:
            m=md.get(l['ticker'])
            if m and m.get('status') in ['settled','finalized'] and m.get('result') in ['yes','no','scalar']:
                if m['result']=='scalar':
                    if m.get('settlement_value_dollars') is None:continue
                    p=float(m['settlement_value_dollars']);p=p if l['side']=='yes' else 1-p
                else:p=1 if m['result']==l['side'] else 0
                q=l['bought']-l['sold']
                if q:ledger_fill(state,t,l,'sell',p,q,'settlement',now,'Official final result after limit replay')
                l['settled']=True
        if t['status']=='entering' and (now>=ts(t['entry_deadline']) or any(l.get('settled') for l in t['legs'])):
            t['reserved']=0
            if t['cost']==0:t['status']='cancelled';t['exit_reason']='Entry expired without sufficient qualifying prints';t['closed_at']=iso(min(now,ts(t['entry_deadline'])))
            else:close_at_book(state,t,books,now,'Unpaired/partial entry timeout; next observed book',cfg['participation'])
        if t['status']=='open':
            mark_trade(t,books)
            maximum=max(0,(t['quantity']-t['cost'])/max(t['cost'],.0001))
            t['target_ratio']=min(cfg['profit_target'],maximum*.5);t['target_profit']=money(t['cost']*t['target_ratio'])
            reason=None
            if now>=cutoff(state,t,cfg):reason='Time exit at next observed book (not backfilled)'
            elif not t['unpriced'] and t['pnl']<=-cfg['loss_trigger']*t['cost']:reason='Loss trigger at observed book (not continuous)'
            elif t.get('profit_recovered_at'):reason='Limit profit recovered; residual exit at observed book'
            elif not t['unpriced'] and t['pnl']>=t['target_profit'] and t['mode']=='taker':reason='Combined net profit target at observed book'
            if reason:close_at_book(state,t,books,now,reason,cfg['participation'])
            else:arm_exits(state,t,cfg,now)
        elif t['status']=='closing':close_at_book(state,t,books,now,t.get('exit_reason','Close remaining'),cfg['participation'])
        finalize(t,now)
    state['backfill']=stats
    state.setdefault('collection_history',[]).append(stats)
    return prints

def run():
    ERRORS.clear()
    now=time.time(); cfg=json.loads((ROOT/'data/config.json').read_text());path=ROOT/'data/state.json'
    state=json.loads(path.read_text()) if path.exists() else initial_state(now,cfg)
    universe=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for ms in pool.map(lambda x:safe(series_markets,x),cfg['series']):universe+=ms or []
    # Retain settled/closed/open positions even when no longer listed as open.
    have={m['ticker'] for m in universe}
    for t in state['trades']:
        if t['status'] not in ['closed','cancelled']:
            for l in t['legs']:
                if l['ticker'] not in have:
                    d=safe(get,'/markets/'+l['ticker'])
                    if d:universe.append(d['market']);have.add(l['ticker'])
    cs=candidates(universe,now,cfg)
    # At most two events per horizon; maker/taker evaluated on the identical candidate set.
    selected=[]
    for h in cfg['horizons']:selected+=sorted([c for c in cs if c['horizon']==h],key=lambda c:c['score'],reverse=True)[:2]
    needed={m['ticker'] for c in selected for m,_ in c['legs']}
    needed|={l['ticker'] for t in state['trades'] if t['status'] not in ['closed','cancelled'] for l in t['legs']}
    books={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for ticker,b in zip(sorted(needed),pool.map(lambda x:safe(readbook,x),sorted(needed))):
            if b:books[ticker]=b
    # Orders become active only after the source quotes were actually received.
    collection_started_at=iso(now);now=time.time()
    prints=process(state,books,universe,now,cfg)
    now=time.time()  # New orders cannot predate completion of this historical retrieval.
    # Mark before permitting new risk; missing books never create optimistic gains.
    summary=summarize(state,cfg,books,now)
    data_gap=bool(state['runs'] and now-ts(state['updated_at'])>(cfg['poll_minutes']+60)*60)
    if now<ts(state['trial_end']) and not summary['paused_for_risk'] and not summary['valuation_incomplete'] and not ERRORS:
        for c in selected:
            for mode in ['taker','maker']:enter(state,c,mode,books,now,cfg)
    state['equity_history'].pop();summarize(state,cfg,books,now)
    state['runs']+=1;state['updated_at']=iso(now)
    state['health']={'backfill':state['backfill'],'errors':ERRORS,'data_gap':data_gap,'market_count':len(universe),'book_count':len(books),'requested_interval_minutes':cfg['poll_minutes'],'status':'degraded' if ERRORS else 'ok','note':f"{cfg['poll_minutes']/60:g}-hour snapshot collection with chronological resting-limit replay. Market stops/time exits require observed books; GitHub schedules may be delayed."}
    state['candidates']=[{'horizon':c['horizon'],'strategy':c['kind'],'event':c['event'],'cost_per_pair':round(c['indicative_cost'],4),'legs':[{'ticker':m['ticker'],'side':side,'title':m['title'],'ask':float(m[side+'_ask_dollars'])} for m,side in c['legs']]} for c in selected]
    # Archive source evidence. These are observations, not fabricated history.
    day=iso(now)[:10];obs={'at':iso(now),'collection_started_at':collection_started_at,'books':books,'markets':[{k:m.get(k) for k in FIELDS} for m in universe if m['ticker'] in needed],'trade_prints':[{'ticker':k[0],'after':k[1],'prints':v} for k,v in prints.items()],'errors':ERRORS}
    folder=ROOT/'data/observations';folder.mkdir(parents=True,exist_ok=True)
    with (folder/(day+'.jsonl')).open('a') as f:f.write(json.dumps(obs,separators=(',',':'))+'\n')
    state['config']=cfg
    state['diagnostics']=diagnostics(state)
    state['reviews']=json.loads((ROOT/'data/reviews.json').read_text())
    path.write_text(json.dumps(state,indent=2)+'\n')
    (ROOT/'docs/data/dashboard.json').write_text(json.dumps(state,separators=(',',':'))+'\n')
    print(json.dumps({'runs':state['runs'],'equity':state['summary']['equity'],'trades':len(state['trades']),'open':state['summary']['open'],'candidates':len(selected),'errors':ERRORS}))
if __name__=='__main__':run()
