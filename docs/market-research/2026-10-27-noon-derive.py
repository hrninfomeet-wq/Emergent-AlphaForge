import json, datetime as dt, statistics as st
from pathlib import Path
P=Path(__file__).parent
IST=dt.timezone(dt.timedelta(hours=5,minutes=30))
raw=json.loads((P/'2026-10-27-noon-snapshot.json').read_text())
old=json.loads((P/'2026-10-27-derived.json').read_text())
groups={}
for b in raw['response']['bars']:
 t=dt.datetime.fromtimestamp(b['ts']/1000,IST)
 groups.setdefault(t.date().isoformat(),[]).append(dict(b,hm=t.strftime('%H:%M')))
today=groups['2026-10-27']
assert today[-1]['hm']=='12:04'
five=[];fifteen=[]
for n,out in [(5,five),(15,fifteen)]:
 for i in range(0,len(today),n):
  block=today[i:i+n]
  if len(block)!=n:continue
  assert all(b['ts']==block[0]['ts']+j*60000 for j,b in enumerate(block))
  out.append(dict(start=block[0]['hm'],end=dt.datetime.fromtimestamp((block[-1]['ts']+60000)/1000,IST).strftime('%H:%M'),open=block[0]['open'],high=max(b['high'] for b in block),low=min(b['low'] for b in block),close=block[-1]['close']))
rows=[]
for r in old['rows'][-10:]:
 b=next(x for x in groups[r['date']] if x['hm']=='12:04')
 rows.append(dict(date=r['date'],anchor=b['close'],official_close=r['official']['close'],residual=r['official']['close']-b['close'],ret=r['official']['close']/b['close']-1))
rets=sorted(r['ret'] for r in rows)
def quant(q):
 pos=(len(rets)-1)*q;i=int(pos);j=min(i+1,len(rets)-1)
 return rets[i]+(rets[j]-rets[i])*(pos-i)
anchor=today[-1]['close']
analysis=json.loads((P/'2026-10-27-noon-analysis.json').read_text(encoding='utf-8-sig'))
chain=analysis['options']['chain']
payouts=[dict(strike=x['strike'],payout=sum(max(x['strike']-r['strike'],0)*r['ce_oi']+max(r['strike']-x['strike'],0)*r['pe_oi'] for r in chain)) for x in chain]
later=[b for b in fifteen if b['end']>'10:43']
out=dict(cutoff='12:05',anchor=anchor,as_of=analysis['as_of'],decision_spot=analysis['spot'],five_minute=five,fifteen_minute=fifteen,original_invalidation_breaches=[b for b in later if b['close']<22685 or b['close']>22850],rows=rows,median_residual=st.median(r['residual'] for r in rows),median_abs_residual=st.median(abs(r['residual']) for r in rows),residual_min=min(r['residual'] for r in rows),residual_max=max(r['residual'] for r in rows),historical_point=anchor*(1+st.median(rets)),empirical_10_90=[anchor*(1+quant(.1)),anchor*(1+quant(.9))],positive=sum(r['ret']>0 for r in rows),negative=sum(r['ret']<0 for r in rows),chain_pcr=sum(r['pe_oi'] for r in chain)/sum(r['ce_oi'] for r in chain),payouts=payouts,chain=chain)
(P/'2026-10-27-noon-derived.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
