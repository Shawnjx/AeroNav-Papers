#!/usr/bin/env python3
"""One-paragraph GLM briefing over the papers admitted in this run.
Shown as the site banner and pushed to WeChat via ServerChan (notify_serverchan.py).
One extra call per run; empty string on any failure so callers can skip."""
import os, re

def write_briefing(kind,batch):
    if not os.getenv("OPENAI_API_KEY") or not batch:return ""
    for k in ("OPENAI_BASE_URL","OPENAI_MODEL"):
        if not os.getenv(k,"").strip():os.environ.pop(k,None)
    try:
        from openai import OpenAI
        client=OpenAI()
        rows=[]
        for b in batch[:20]:
            extra=f"，被引{b['cit']}" if b.get("cit") else ""
            rows.append(f"- {b['t']}（相关{b.get('r') or '?'}{extra}）：{b.get('s') or ''}")
        scope="今日新入库" if kind=="daily" else "本轮新入库的近五年高被引经典"
        prompt=f"你是具身导航论文情报站的主编。下面是{scope}的论文。写一段面向研究者的中文速览（120-200字，单个自然段）：先概括这批工作的主线，再点出2-3篇最值得读的并说明理由，结尾一句趋势观察。不要罗列全部条目，不要使用列表、序号或小标题，不要空洞形容词；论文保留英文原名，其余用中文。\n"+"\n".join(rows)
        r=client.chat.completions.create(model=os.getenv("OPENAI_MODEL","gpt-4.1-mini"),messages=[{"role":"user","content":prompt}],temperature=.4)
        return re.sub(r"\s*\n\s*"," ",r.choices[0].message.content.strip())
    except Exception as e:
        print("briefing fallback:",type(e).__name__);return ""
