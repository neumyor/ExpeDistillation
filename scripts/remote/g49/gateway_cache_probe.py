"""Does the gateway's Qwen reuse a cached PREFIX across the turns of a conversation?
Turn 2 = turn 1 + assistant + new user; explicit cache_control on the last user message each turn."""
import json, os, sys, time, urllib.request
base, key, model = os.environ["EXPEDISTILL_LLM_ENDPOINT"], os.environ["EXPEDISTILL_LLM_API_KEY"], sys.argv[1]
mode = sys.argv[2]  # explicit | implicit
filler = " ".join(f"Rule {i}: keep notes about item {i} short and exact." for i in range(400))
nonce = f"probe-{int(time.time())}"
def blk(t, mark): 
    b = {"type": "text", "text": t}
    if mark and mode == "explicit": b["cache_control"] = {"type": "ephemeral"}
    return [b]
def call(msgs):
    body = {"model": model, "max_tokens": 8, "temperature": 0, "messages": msgs}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    u = json.load(urllib.request.urlopen(req, timeout=120))["usage"]
    d = u.get("prompt_tokens_details") or {}
    return u["prompt_tokens"], d.get("cached_tokens"), d.get("cache_creation_input_tokens") or u.get("cache_creation_input_tokens")
sysm = {"role": "system", "content": f"{nonce} You are a careful assistant. {filler}"}
t1 = [sysm, {"role": "user", "content": blk("Question one: what is 2+3?", True)}]
t2 = t1 + [{"role": "assistant", "content": "5"}, {"role": "user", "content": blk("Question two: what is 4+7?", True)}]
t3 = t2 + [{"role": "assistant", "content": "11"}, {"role": "user", "content": blk("Question three: what is 6+9?", True)}]
for name, m in (("turn1", t1), ("turn2 (turn1 + 2 msgs)", t2), ("turn3 (turn2 + 2 msgs)", t3), ("turn3 again (identical)", t3)):
    print(mode, name, "prompt/cached/created =", call(m)); time.sleep(3)
