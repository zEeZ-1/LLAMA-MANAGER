#!/usr/bin/env python3
"""Measure a running release server from request POST to the first generated token."""
import argparse
import json
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

def measure(client, url, case, prompt, count, trial, label):
    data=json.dumps(dict(prompt=prompt,n_predict=count,temperature=0,seed=42,stream=True,
                         return_tokens=True,cache_prompt=False,ignore_eos=True)).encode()
    req=Request(url+'/completion',data=data,headers={'Content-Type':'application/json'})
    first=last=None
    first_burst=0
    ids=[]
    timings={}
    start=time.monotonic()
    with client.open(req,timeout=300) as stream:
        for line in stream:
            if not line.startswith(b'data:'):continue
            item=line[5:].strip()
            if item==b'[DONE]':break
            msg=json.loads(item)
            if 'error' in msg:raise RuntimeError(msg['error'])
            tokens=msg.get('tokens',[])
            if tokens and not msg.get('stop'):
                last=time.monotonic()
                if first is None:
                    first=last
                    first_burst=len(tokens)
                ids.extend(tokens)
            if msg.get('timings'):timings=msg['timings']
    length=4096 if isinstance(prompt,str) else len(prompt)
    if first is None or (timings.get('cache_n'),timings.get('prompt_n'),timings.get('predicted_n'),len(ids))!=(0,length,count,count):
        raise RuntimeError((case,timings,len(ids)))
    if label=='mtp-on' and timings.get('draft_n',0)==0:raise RuntimeError('MTP did not produce candidates')
    if label=='mtp-off' and timings.get('draft_n',0)>0:raise RuntimeError('unexpected draft')
    return dict(mode=label,case=case,trial=trial,input_tokens=length,output_tokens=count,
                ttft_s=first-start,wall_s=time.monotonic()-start,
                first_event_tokens=first_burst,decode_tps=(len(ids)-first_burst)/(last-first),timings=timings,ids=ids)

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--url',default='http://127.0.0.1:18236')
    ap.add_argument('--mode',choices=['mtp-on','mtp-off'],required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--reps',type=int,default=3)
    args=ap.parse_args()
    here=Path(__file__).resolve().parent
    prompts=json.loads((here/'inputs.json').read_text())
    client=build_opener(ProxyHandler({}))
    order=['log128','log1024','log4096','code4096']
    jobs=[('first-raw-code4096',(here/'code.input.txt').read_text(),64,0)]
    for trial in range(1,args.reps+1):
        jobs.extend((case,prompts[case],64,trial) for case in (order if trial%2 else order[::-1]))
    jobs.append(('code4096-long-output',prompts['code4096'],128,1))
    with args.output.open('x') as out:
        for case,prompt,count,trial in jobs:
            row=measure(client,args.url,case,prompt,count,trial,args.mode)
            out.write(json.dumps(row)+'\n');out.flush()
            print(f"{case} trial {trial} TTFT {row['ttft_s']:.3f}s decode {row['decode_tps']:.2f} tok/s "
                  f"draft {row['timings'].get('draft_n_accepted',0)}/{row['timings'].get('draft_n',0)}",flush=True)
