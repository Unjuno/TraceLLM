"""CPU study on artificial fixtures ONLY; no production data-dir argument.

Run: PYTHONPATH=src python benchmarks/cpu_study.py --section speed --output result.json
Quality results are exploratory; templates overlap, not evidence of diary fluency.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import resource
import statistics as st
import subprocess
import sys
import tempfile
import time

import torch
from trace_llm.engine import Engine
from trace_llm.evaluation import ByteNgram
from trace_llm.model import Config, GPT, optimizer
from trace_llm.training import TrainingConfig, backward_examples, model_config


def summary(values):
    return dict(median=st.median(values), min=min(values), max=max(values), samples=values)


def hardware():
    info = Path('/proc/cpuinfo').read_text() if Path('/proc/cpuinfo').exists() else ''
    names = [s.split(':', 1)[1].strip() for s in info.splitlines() if s.startswith('model name')]
    clocks = [float(s.split(':')[1]) for s in info.splitlines() if s.startswith('cpu MHz')]
    return dict(cpu=names[0] if names else platform.processor(), logical_cpus=os.cpu_count(),
                platform=platform.platform(), python=sys.version, torch=torch.__version__,
                precision='FP32', device='cpu', clock_control='not pinned; shared VM',
                clock_mhz_snapshot=clocks, torch_build=torch.__config__.show())


def speed(preset='seed', context=256, threads=2, mode='separate', optim='adamw'):
    torch.set_num_threads(threads); torch.manual_seed(11)
    model = GPT(model_config(preset, context))
    opt = optimizer(model) if optim == 'adamw' else torch.optim.SGD(model.parameters(), lr=3e-4, momentum=.9)
    examples = [(torch.randint(0,256,(n,)).tolist(), torch.randint(0,256,(n,)).tolist())
                for n in (context, max(1,context//3))]
    def step():
        opt.zero_grad(set_to_none=True); backward_examples(model, examples, mode)
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0); opt.step()
    for _ in range(3): step()
    wall, cpu = [], []
    for _ in range(9):
        t,c=time.perf_counter(),time.process_time(); step()
        wall.append((time.perf_counter()-t)*1000); cpu.append((time.process_time()-c)*1000)
    params=sum(p.numel() for p in model.parameters())
    states=[v for s in opt.state.values() for v in s.values() if isinstance(v,torch.Tensor)]
    return dict(preset=preset, parameters=params, context=context, threads=threads, mode=mode,
                optimizer=optim, batch='1 new + 1 shorter replay', warmup_steps=3,
                effective_targets=sum(len(y) for _,y in examples), step_ms=summary(wall),
                process_cpu_ms=summary(cpu), model_bytes=params*4, gradient_bytes=params*4,
                optimizer_bytes=sum(t.numel()*t.element_size() for t in states),
                peak_process_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                rss_scope='fresh worker, includes Python/PyTorch, Linux units')


def fixtures():
    a=[f'人工記録{i}。今日は{topic}について考えた。最初は条件を増やす案を考えたが、'
       f'原因を調べるため一つに絞った。{detail}を記録し、明日は結果を比較する。'
       f'記録番号は{i}。急いで結論を出さず、観察と推測を分けて書く。'
       for i,(topic,detail) in enumerate(zip(['実験','読書','文章','計画','設計','測定'],
                                            ['手順','感想','理由','時間','選択','変化']))]
    b=[f'人工観察{i}。今日は{plant}の様子を見た。昨日より{state}ので、'
       f'水の量を変えて経過を記録した。午後は庭を歩き、葉と土を確認した。'
       f'観察番号は{i}。明日も同じ場所で変化を確かめたい。'
       for i,(plant,state) in enumerate(zip(['花','木','草','種','苗','枝'],
           ['乾いている','明るい','暖かい','湿っている','寒い','緑が濃い']))]
    return a[:4],b[:4],a[4:]+b[4:]


def quality(preset='seed',context=256,replay=.25,lr=3e-4,repeats=2,seed=11):
    import random
    torch.set_num_threads(2)
    a,b,held=fixtures()
    policy=TrainingConfig(replay_ratio=replay,learning_rate=lr,max_steps=4096,max_train_seconds=0)
    with tempfile.TemporaryDirectory(prefix='trace-artificial-quality-') as root:
        e=Engine(Path(root),config=model_config(preset,context),training=policy)
        try:
            payload=e.load_current()
            with torch.random.fork_rng(devices=[]):
                torch.manual_seed(seed); m=GPT(model_config(preset,context))
                payload.update(model=m.state_dict(),optimizer=optimizer(m,lr).state_dict(),
                               torch_rng=torch.get_rng_state(),sampler_rng=random.Random(seed).getstate())
            cp=e.status()['checkpoint_id'];e._publish(payload,cp,cp,'artificial-test-seed')
            ng=ByteNgram(); obs=[]; old_before=None; total_wall=0
            for index,text in enumerate(a+b):
                ng_before=ng.score(text);p=e.create_page('ARTIFICIAL',text)
                e.feed(p['id'],1,repeats,f'fixture-{index}')
                t=time.perf_counter();e.train_next();total_wall+=time.perf_counter()-t
                j=e.history()['jobs'][0]
                if j['status']!='succeeded': raise RuntimeError(j['error'])
                obs.append(dict(stage='A' if index<len(a) else 'B',metrics=j['metrics'],ngram_before=ng_before))
                ng.update(text)
                if index==len(a)-1:
                    snap=e.load_current();m=GPT(Config(**snap['config']));m.load_state_dict(snap['model'])
                    old_before=st.mean(e._score(m,t) for t in a)
            snap=e.load_current();m=GPT(Config(**snap['config']));m.load_state_dict(snap['model'])
            return dict(preset=preset,context=context,replay=replay,lr=lr,repeats=repeats,seed=seed,
                        threads=2,parameters=sum(p.numel() for p in m.parameters()),observations=obs,
                        old_before=old_before,old_after=st.mean(e._score(m,t) for t in a),
                        new_after=st.mean(e._score(m,t) for t in b),
                        heldout=st.mean(e._score(m,t) for t in held),
                        ngram_heldout=st.mean(ng.score(t) for t in held),
                        job_wall_seconds=total_wall,exposures=snap['exposure_totals'])
        finally:e.close()


def e2e():
    torch.set_num_threads(2);a,b,_=fixtures();rows=[]
    for mode in ('padded','separate'):
        startup,jobs,generate,cpu,lengths=[],[],[],[],[]
        for _ in range(7):
            with tempfile.TemporaryDirectory(prefix='trace-artificial-e2e-') as root:
                t=time.perf_counter();e=Engine(Path(root),training=TrainingConfig(batch_mode=mode))
                startup.append(time.perf_counter()-t)
                try:
                    p=e.create_page('ARTIFICIAL A',a[0]);e.feed(p['id'],1,2,'a');e.train_next()
                    p=e.create_page('ARTIFICIAL B',b[0]);e.feed(p['id'],1,2,'b')
                    t,c=time.perf_counter(),time.process_time();e.train_next()
                    jobs.append(time.perf_counter()-t);cpu.append(time.process_time()-c)
                    assert e.history()['jobs'][0]['status']=='succeeded'
                    t=time.perf_counter();r=e.respond(p['id'],1,64,0.,11)
                    generate.append(time.perf_counter()-t);lengths.append(len(r['raw_tokens']))
                finally:e.close()
        rows.append(dict(mode=mode,threads=2,context=256,repeats=2,startup_seconds=summary(startup),
                         job_seconds=summary(jobs),cpu_seconds=summary(cpu),generation_seconds=summary(generate),
                         generated_tokens=lengths,scope='job includes load/eval/train/fsync/DB; imports excluded'))
    return rows


def compile_probe():
    torch.set_num_threads(2);torch.manual_seed(11)
    m=GPT(Config(context=128));opt=optimizer(m)
    x=torch.randint(0,256,(1,128));y=torch.randint(0,256,(1,128))
    def step(fn):
        opt.zero_grad(set_to_none=True)
        torch.nn.functional.cross_entropy(fn(x).flatten(0,1),y.flatten()).backward();opt.step()
    t=time.perf_counter();fn=torch.compile(m);step(fn);cold=time.perf_counter()-t
    times=[]
    for _ in range(5):
        t=time.perf_counter();step(fn);times.append(time.perf_counter()-t)
    return dict(cold_compile_train_seconds=cold,warm_step_seconds=summary(times),
                threads=2,context=128,batch_size=1,backend='inductor')


def cold():
    torch.set_num_threads(2)
    with tempfile.TemporaryDirectory(prefix='trace-cold-artificial-') as root:
        t=time.perf_counter(); e=Engine(Path(root))
        init=time.perf_counter()-t
        try:
            p=e.create_page('ARTIFICIAL',fixtures()[0][0]);e.feed(p['id'],1,2,'cold')
            t=time.perf_counter();e.train_next();job=time.perf_counter()-t
            assert e.history()['jobs'][0]['status']=='succeeded'
            return dict(engine_initialization_seconds=init,first_job_seconds=job,threads=2,
                        scope='parent process wall time includes Python/PyTorch imports')
        finally:e.close()


def worker(section,params,timeout=120):
    env=dict(os.environ,TORCHINDUCTOR_COMPILE_THREADS='1')
    with tempfile.TemporaryDirectory(prefix='trace-compile-cache-') as cache:
        env['TORCHINDUCTOR_CACHE_DIR']=cache
        try:
            started=time.perf_counter()
            p=subprocess.Popen([sys.executable,__file__,'--worker',section,'--params',json.dumps(params)],
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,
                               start_new_session=True)
            stdout,stderr=p.communicate(timeout=timeout)
            result=json.loads(stdout) if p.returncode==0 else dict(error=stderr[-2500:],params=params)
            result['worker_wall_seconds']=time.perf_counter()-started
            return result
        except subprocess.TimeoutExpired:
            import signal
            os.killpg(p.pid,signal.SIGKILL)
            p.communicate()
            return dict(status='worker_timeout',timeout_seconds=timeout,
                        note='includes imports/compile; no completed timing')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--section',choices=['speed','quality','e2e','compile','cold'],default='speed')
    parser.add_argument('--output',type=Path,default=Path('cpu-results.json'))
    parser.add_argument('--worker',choices=['speed','quality','compile','cold']);parser.add_argument('--params',default='{}')
    args=parser.parse_args()
    if args.worker:
        fn={'speed':speed,'quality':quality,'compile':compile_probe,'cold':cold}[args.worker]
        print(json.dumps(fn(**json.loads(args.params)),ensure_ascii=False));return
    result=dict(hardware=hardware(),section=args.section,measurements=[],
                scope='artificial shared-template fixtures, no real diaries or fluency evidence')
    if args.section=='speed':
        specs=[dict(preset=p,context=c) for p in ('seed','medium','small') for c in (256,512,1024)]
        specs += [dict(threads=t,mode=m) for t in (1,2,4) for m in ('padded','separate')]
        specs += [dict(optim='sgd')]
    elif args.section=='quality':
        import hashlib
        a,b,h=fixtures()
        result['corpus']=dict(train_bytes=sum(len(t.encode()) for t in a+b),heldout_bytes=sum(len(t.encode()) for t in h),
            train_pages=len(a+b),heldout_pages=len(h),exact_page_overlap=bool(set(a+b)&set(h)),shared_templates=True,
            sha256=hashlib.sha256(json.dumps([a,b,h],ensure_ascii=False).encode()).hexdigest(),
            unit='nats/target incl EOS; page-macro mean')
        specs=[dict(**p,seed=s) for p in ({},{'preset':'medium'},{'preset':'small'}, {'replay':0},
              {'replay':.5},{'lr':1e-3},{'repeats':6},{'context':512},{'context':1024}) for s in (11,29,47)]
    else: specs=[]
    for spec in specs:
        result['measurements'].append(worker(args.section,spec))
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    if args.section=='e2e': result['measurements']=e2e()
    if args.section=='cold': result['measurements']=[worker('cold',{}) for _ in range(3)]
    if args.section=='compile': result['measurements']=[worker('compile',{},45)]
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(dict(section=args.section,cases=len(result['measurements']),output=str(args.output))))


if __name__=='__main__':main()
