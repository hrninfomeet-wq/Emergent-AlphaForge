import csv, datetime as dt, io, json, statistics as st, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT=Path(__file__).parent
IST=dt.timezone(dt.timedelta(hours=5,minutes=30))
d=json.loads((ROOT/'2026-10-27-nifty-snapshot.json').read_text())
groups={}
for b in d['response']['bars']:
    t=dt.datetime.fromtimestamp(b['ts']/1000,IST)
    groups.setdefault(t.date().isoformat(),[]).append(dict(b,ist=t.isoformat(),hm=t.strftime('%H:%M')))
dates=sorted(x for x in groups if x<'2026-10-27')
def official(day):
    name=dt.date.fromisoformat(day).strftime('%d%m%Y')
    url=f'https://nsearchives.nseindia.com/content/indices/ind_close_all_{name}.csv'
    p=ROOT/f'{day}-nse-official.csv'
    try:
        if p.exists(): raw=p.read_text(encoding='utf-8-sig')
        else:
            raw=urllib.request.urlopen(url,timeout=20).read().decode('utf-8-sig')
            p.write_text(raw,encoding='utf-8')
        rows=list(csv.DictReader(io.StringIO(raw)))
        row=next(r for r in rows if r['Index Name'].strip()=='Nifty 50')
        assert dt.datetime.strptime(row['Index Date'],'%d-%m-%Y').date().isoformat()==day
        return day,{k:float(row[v]) for k,v in [('open','Open Index Value'),('high','High Index Value'),('low','Low Index Value'),('close','Closing Index Value')]}
    except Exception as e:return day,{'error':str(e)}
officials=dict(ThreadPoolExecutor(max_workers=4).map(official,dates))
rows=[]
for day in dates:
    bars=groups[day];o=officials[day]
    row=dict(date=day,count=len(bars),first=bars[0]['hm'],last=bars[-1]['hm'],official=o)
    anchor=next((b for b in bars if b['hm']=='10:39'),None)
    pre=next((b for b in bars if b['hm']=='15:14'),None)
    if 'close' in o:
        row.update(range=o['high']-o['low'],warehouse_close_difference=bars[-1]['close']-o['close'])
        if anchor:row.update(anchor=anchor['close'],residual=o['close']-anchor['close'],ret=o['close']/anchor['close']-1)
        if pre:row['closing_print_gap']=o['close']-pre['close']
    rows.append(row)
today=groups['2026-10-27']
cut=[b for b in today if b['hm']<='10:39']
five=[]
for i in range(0,len(cut),5):
    bs=cut[i:i+5]
    if len(bs)==5:five.append(dict(time=bs[0]['hm'],open=bs[0]['open'],high=max(b['high'] for b in bs),low=min(b['low'] for b in bs),close=bs[-1]['close']))
eligible=[r for r in rows if 'ret' in r][-10:]
def stats(xs):return dict(n=len(xs),median=st.median(xs),mean=st.mean(xs),min=min(xs),max=max(xs),median_abs=st.median(map(abs,xs)))
anchor=cut[-1]['close']
returns=sorted(r['ret'] for r in eligible)
def quantile(q):
    pos=(len(returns)-1)*q;low=int(pos);high=min(low+1,len(returns)-1)
    return returns[low]+(returns[high]-returns[low])*(pos-low)
out=dict(retrieved_at=d['retrieved_at_utc'],cutoff='10:40',rows=rows,today_count=len(today),today_first=today[0]['ist'],today_last=today[-1]['ist'],cutoff_anchor=anchor,cutoff_anchor_time=cut[-1]['ist'],five_minute=five,history_dates=[r['date'] for r in eligible],residuals=stats([r['residual'] for r in eligible]),daily_ranges=stats([r['range'] for r in eligible]),closing_print=stats([r['closing_print_gap'] for r in eligible]),historical_point=anchor*(1+st.median(returns)),empirical_10_90=[anchor*(1+quantile(.1)),anchor*(1+quantile(.9))],no_change=anchor,zero_volume=all(b['volume']==0 for b in cut))
(ROOT/'2026-10-27-derived.json').write_text(json.dumps(out,indent=2))
math_input=dict(current_level=anchor,previous_close=officials['2026-10-26']['close'],open=22708.8,high=22804.5,low=22653.2,recent_daily_ranges=[r['range'] for r in eligible],same_time_to_official_close_moves=[r['residual'] for r in eligible])
(ROOT/'2026-10-27-math-input.json').write_text(json.dumps(math_input,indent=2))
print(json.dumps(out,indent=2))
