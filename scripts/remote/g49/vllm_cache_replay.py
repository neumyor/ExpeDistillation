"""Replay recorded Qwen requests with a fresh nonce in the system message (so no
earlier cache content matches) through the load balancer (mode lb) or by random
choice among the replicas (mode random), and sum the replicas' prefix-cache
counters. Replicas listen on 127.0.0.1:8110-8112 and the balancer on 8010 (see
start_vllm_replicas.sh). Use workers 1: the recorded order is cause before
effect only when requests are replayed one at a time.
usage: vllm_cache_replay.py <mode: lb|random> <nonce> <runs_dir> <key> <workers>"""
import glob, json, random, sys, time, urllib.request, concurrent.futures as cf
mode, nonce, runs_dir, key, workers = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
REPL = ["http://127.0.0.1:8110", "http://127.0.0.1:8111", "http://127.0.0.1:8112"]
LB = "http://127.0.0.1:8010"
rows = []
for f in sorted(glob.glob(runs_dir + "/llm_requests/0*.json")):
    r = json.load(open(f))
    if r.get("status") == "ok" and r["role"] == "executor_weak":
        rows.append(r)
def metrics(base):
    txt = urllib.request.urlopen(base + "/metrics", timeout=30).read().decode(); out = [0.0, 0.0]
    for l in txt.splitlines():
        if l.startswith("vllm:prefix_cache_queries_total"): out[0] += float(l.rsplit(" ", 1)[1])
        if l.startswith("vllm:prefix_cache_hits_total"): out[1] += float(l.rsplit(" ", 1)[1])
    return out
def post(r):
    base = LB if mode == "lb" else random.choice(REPL)
    body = {"model": "qwen3.6-35b-a3b", "max_tokens": 1, "temperature": 0,
            "messages": [{"role": "system", "content": r["system"] + " [" + nonce + "]"},
                         {"role": "user", "content": r["request_text"]}],
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    return json.load(urllib.request.urlopen(req, timeout=900))["usage"]["prompt_tokens"]
before = [metrics(b) for b in REPL]; t0 = time.time(); tot = 0
with cf.ThreadPoolExecutor(workers) as ex:
    for n in ex.map(post, rows): tot += n
after = [metrics(b) for b in REPL]
q = [a[0] - b[0] for a, b in zip(after, before)]; h = [a[1] - b[1] for a, b in zip(after, before)]
print(mode, "workers", workers, "prompt tokens", tot, "per-replica queries", [int(x) for x in q],
      "hit rate %.1f%%" % (100 * sum(h) / sum(q)), "secs %.0f" % (time.time() - t0))
