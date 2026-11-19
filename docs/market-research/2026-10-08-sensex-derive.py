import csv, datetime as dt, json, statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
snap = json.loads((ROOT / '2026-10-08-sensex-alphaforge.json').read_text())
refresh_path = ROOT / '2026-10-08-sensex-latest.json'
if refresh_path.exists():
    fresh = json.loads(refresh_path.read_text())
    first_ts = fresh['response']['bars'][0]['ts']
    snap['response']['bars'] = [b for b in snap['response']['bars'] if b['ts'] < first_ts] + fresh['response']['bars']
    snap['quality'] = fresh['quality']
official = {}
for r in csv.DictReader((ROOT / '2026-10-08-bse-official-history.csv').read_text().splitlines()):
    day = dt.datetime.strptime(r['Date'], '%d-%B-%Y').date().isoformat()
    official[day] = {k.lower(): float(v) for k, v in r.items() if k != 'Date'}
days = {}
for b in snap['response']['bars']:
    stamp = dt.datetime.fromtimestamp(b['ts']/1000, IST)
    days.setdefault(stamp.date().isoformat(), {})[stamp.strftime('%H:%M')] = b
latest = snap['quality']['last_completed_continuous_bar']
stamp = dt.datetime.fromisoformat(latest['timestamp_ist'])
today = stamp.date().isoformat()
anchor_time = stamp.strftime('%H:%M')
cutoff = (stamp + dt.timedelta(minutes=1)).strftime('%H:%M')
rows, excluded, discrepancies = [], [], []
for day in sorted(official):
    bars = days.get(day, {})
    if anchor_time not in bars:
        excluded.append({'date': day, 'reason': 'missing exact anchor'})
        continue
    o = official[day]
    row = {'date': day, 'anchor': bars[anchor_time]['close'], 'anchor_available_ist': cutoff,
           'anchor_bar_timestamp': f'{day}T{anchor_time}:00+05:30', 'anchor_bar_minutes': 1,
           'anchor_timestamp_label': 'start', 'official_close': o['close'], 'high': o['high'],
           'low': o['low'], 'regime': 'post_CAS'}
    if '15:14' in bars:
        row.update(pre_close=bars['15:14']['close'], pre_close_available_ist='15:15')
    rows.append(row)
    if '15:29' in bars and abs(bars['15:29']['close']-o['close']) > .02:
        discrepancies.append({'date':day, 'warehouse_last':bars['15:29']['close'], 'official':o['close']})
payload = {'instrument': 'SENSEX spot', 'cutoff_ist': cutoff, 'timezone': 'Asia/Kolkata',
           'target':'official_close', 'as_of_date':today, 'regime':'post_CAS', 'rows':rows,
           'sources':['AlphaForge Upstox minute candles','BSE ProduceCSVForDate official daily archive'],
           'close_validation':'Exact start-labelled one-minute anchors; independent BSE official targets; current unfinished session excluded; holidays not forward-filled; backfilled history is research-grade.'}
(ROOT / '2026-10-08-sensex-input.json').write_text(json.dumps(payload,indent=2))
def quantile(vals, q):
    vals=sorted(vals); p=(len(vals)-1)*q; lo=int(p); hi=min(lo+1,len(vals)-1)
    return vals[lo]+(vals[hi]-vals[lo])*(p-lo)
spot=latest['close']; recent=rows[-10:]
returns=[r['official_close']/r['anchor']-1 for r in recent]
todaybars=[b for t,b in sorted(days[today].items()) if t<=anchor_time]
prev=official[sorted(official)[-1]]['close']
high=max(b['high'] for b in todaybars); low=min(b['low'] for b in todaybars)
warehouse_extremes = {'open':todaybars[0]['open'],'high':high,'low':low}
header=json.loads((ROOT/'2026-10-08-market-header-1421.json').read_text())
header_sensex=next(x for x in header['items'] if x['key']=='sensex')
session_open=header_sensex['open']
high=max(high,header_sensex['high']); low=min(low,header_sensex['low'])
buckets={}
for t,b in sorted(days[today].items()):
    minute=int(t[:2])*60+int(t[3:]); k=minute-minute%5
    if k+5>int(cutoff[:2])*60+int(cutoff[3:]): continue
    buckets.setdefault(k,[]).append(b)
five=[{'time':f'{k//60:02}:{k%60:02}', 'open':v[0]['open'],'high':max(x['high'] for x in v),
       'low':min(x['low'] for x in v),'close':v[-1]['close']} for k,v in buckets.items() if len(v)==5]
cas=[r['official_close']-r['pre_close'] for r in recent if 'pre_close' in r]
out={'retrieved_ist':snap['quality']['as_of_ist'],'latest':latest,'cutoff':cutoff,'source_sessions':len(rows),
     'source_dates':[rows[0]['date'],rows[-1]['date']], 'excluded':excluded,'warehouse_target_discrepancies':discrepancies,
     'warehouse_extremes_not_official':warehouse_extremes,
     'day_extremes_source':'AlphaForge Upstox market-header open/high corroborated by Moneycontrol; low extended with later completed candles. Stored candles omit opening extremes despite complete minute count; cause unproven.',
     'current':{'spot':spot,'prior_close':prev,'prior_date':sorted(official)[-1],'open':session_open,
       'high':high,'low':low,'range':high-low,'change':spot-prev,'change_pct':100*(spot/prev-1),
       'gap':session_open-prev,'range_location':(spot-low)/(high-low),
       'bar_count':len(todaybars),'expected_minutes':int((stamp-dt.datetime.combine(stamp.date(),dt.time(9,15),IST)).total_seconds()/60)+1},
     'last10_range_median':st.median([r['high']-r['low'] for r in recent]),
     'last10_residuals':[{'date':r['date'],'move':r['official_close']-r['anchor']} for r in recent],
     'historical_point':spot*(1+st.median(returns)), 'empirical_lower':spot*(1+quantile(returns,.1)),
     'empirical_upper':spot*(1+quantile(returns,.9)),
     'cas_proxy':{'n':len(cas),'median':st.median(cas),'median_abs':st.median(map(abs,cas)),'min':min(cas),'max':max(cas)},
     'recent_five_minute_bars':five[-15:],
     'yesterday_forecast_score':{'point':73000,'actual':prev,'absolute_error':abs(prev-73000),
       'band':[72450,73350],'band_hit':72450<=prev<=73350},
     'recent_daily':[{'date':d,**official[d]} for d in sorted(official)[-10:]]}
(ROOT/'2026-10-08-sensex-derived.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
