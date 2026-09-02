#!/usr/bin/env python3
import hashlib, html, json, os, re, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import feedparser
import requests

from briefing import write_briefing

ROOT=Path(__file__).resolve().parents[1]
CFG=json.loads((ROOT/"config/topics.json").read_text(encoding="utf-8"))
DATA=ROOT/"data/papers.json"
REJECTED=ROOT/"data/rejected.json"
GATE=CFG.get("gate",{})
UA={"User-Agent":"AeroNavPapers/1.0 research literature monitor"}
VENUE_PATTERNS=[v.lower() for v in CFG.get("venue_allowlist",[])]+["pattern analysis and machine intelligence","international journal of computer vision","robotics research","robotics and automation letters","geoscience and remote sensing","computer vision and pattern recognition","international conference on computer vision","european conference on computer vision","neural information processing","learning representations","robot learning","intelligent robots and systems","robotics and automation"]
CATALOG=[t["name"] for t in CFG.get("topics_catalog",[])]

def clean(s): return re.sub(r"\s+"," ",html.unescape(s or "")).strip()
def norm_title(s): return re.sub(r"[^a-z0-9]","",s.lower())
def paper_id(title,arxiv_id="",doi=""):
    return doi.lower() or arxiv_id or hashlib.sha1(norm_title(title).encode()).hexdigest()[:16]

def fetch(url,tries=3):
    for i in range(tries):
        try:return requests.get(url,headers=UA,timeout=35)
        except requests.RequestException:
            if i==tries-1:raise
            time.sleep(6*(i+1))

def fetch_arxiv():
    out=[]
    for query in CFG["queries"]:
        url="https://export.arxiv.org/api/query?"+urlencode({"search_query":query,"start":0,"max_results":30,"sortBy":"submittedDate","sortOrder":"descending"})
        try:feed=feedparser.parse(fetch(url).content)
        except requests.RequestException as e:print("arXiv query failed:",type(e).__name__);time.sleep(3);continue
        for e in feed.entries:
            aid=e.id.rsplit("/",1)[-1].split("v")[0]
            cats=[x.term for x in getattr(e,"tags",[])]
            out.append({"id":paper_id(e.title,aid),"arxiv_id":aid,"title":clean(e.title),"authors":[a.name for a in e.authors],"abstract":clean(e.summary),"published":e.published[:10],"source":"arXiv","venue":"预印本","url":f"https://arxiv.org/abs/{aid}","pdf_url":f"https://arxiv.org/pdf/{aid}","code_url":"","categories":cats})
        time.sleep(3)
    return out

def enrich_s2(p):
    key=os.getenv("S2_API_KEY",""); headers={**UA,**({"x-api-key":key} if key else {})}
    try:
        r=requests.get(f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{p['arxiv_id']}",params={"fields":"title,venue,year,externalIds,openAccessPdf,url,authors,citationCount"},headers=headers,timeout=20)
        if r.status_code!=200:return p
        d=r.json(); venue=clean(d.get("venue"))
        if venue:p["venue"]=venue
        p["citation_count"]=d.get("citationCount",0)
        p["semantic_scholar_url"]=d.get("url","")
        return p
    except requests.RequestException:return p

def classify(p):
    text=(p["title"]+" "+p["abstract"]).lower(); scores={k:sum(2 if w in p["title"].lower() else 1 for w in ws if w in text) for k,ws in CFG["keywords"].items()}
    topics=[k for k,v in sorted(scores.items(),key=lambda x:-x[1]) if v>0][:3]
    return topics or ["具身导航"],sum(scores.values())

def venue_verified(p):return any(x in (p.get("venue") or "").lower() for x in VENUE_PATTERNS)
def code_signal(p):
    return bool(re.search(r"github\.com/[\w.-]+/[\w.-]+|code (is |will be )?(available|released|open)",p["abstract"],re.I))

def evidence(p):
    verified=venue_verified(p)
    text=p["abstract"].lower(); experimental=any(x in text for x in ["experiment","benchmark","dataset","real-world","simulation"])
    if verified and experimental:return "强",f"发表来源命中白名单（{p.get('venue')}），摘要包含实验或基准证据；仍建议阅读全文核对设置。"
    if experimental:return "中","预印本或发表状态尚未确认，但摘要报告了实验/基准验证。"
    return "初步","主要依据预印本摘要，实验规模、消融与复现性需要阅读全文确认。"

def fallback_review(p):
    topic="、".join(p["topics"][:2]); abstract=p["abstract"]
    first=re.split(r"(?<=[.!?])\s+",abstract)[0][:220]
    return {"summary_zh":f"该工作聚焦{topic}，主要研究问题可概括为：{p['title']}。当前自动研判未启用，建议结合英文摘要阅读。","change_zh":f"摘要首句：{first}" if first else "尚无足够摘要信息。","why_it_matters":f"与{topic}直接相关，可重点检查其空间表征、决策闭环和相对现有基线的增益。"}

def llm_review(p):
    if not os.getenv("OPENAI_API_KEY"):return fallback_review(p)
    for k in ("OPENAI_BASE_URL","OPENAI_MODEL"):
        if not os.getenv(k,"").strip():os.environ.pop(k,None)
    try:
        from openai import OpenAI
        client=OpenAI(); prompt=f'''你是具身导航领域的论文评审与情报分析师。先对照研究范围判断该论文是否属于收录范围，再评估方法严谨度，然后只依据题目和摘要用中文输出JSON，不得补造事实。字段：primary_topic（从枚举中选一个最贴切的技术线：{"、".join(CATALOG)}；都不贴切时选最接近的），relevance（0-10整数，与研究范围的相关度：10=直接命中核心问题，7-9=明显相关，5-6=边缘相关，0-4=范围外或仅术语擦边），rigor（0-10整数，方法与实验可信度：有明确实验设置、基线对比、消融与可复现信息=7-10，有实验但描述简略=5-6，纯想法/无实验=0-3），reject_reason（relevance<6或rigor<5时给一句话理由，否则空字符串），summary_zh（60-90字，一句话简介），change_zh（60-100字，相比常见路线真正改变了什么；信息不足就明说），why_it_matters（60-100字，对收录范围内技术线的价值，优先联系UAV主动目标搜索与ObjectNav；范围外写"无直接价值"）。\n研究范围：{CFG.get("scope_note","")}\n题目：{p['title']}\n摘要：{p['abstract']}'''
        r=client.chat.completions.create(model=os.getenv("OPENAI_MODEL","gpt-4.1-mini"),messages=[{"role":"user","content":prompt}],response_format={"type":"json_object"},temperature=.1)
        txt=re.sub(r"^```(?:json)?\s*|\s*```$","",r.choices[0].message.content.strip())
        m=re.search(r"\{.*\}",txt,re.S)
        d=json.loads(m.group(0) if m else txt)
        d["relevance"]=int(d.get("relevance") or 0);d["rigor"]=int(d.get("rigor") or 0)
        if d.get("primary_topic") not in CATALOG:d.pop("primary_topic",None)
        return d
    except Exception as e:
        print("LLM fallback:",type(e).__name__);return fallback_review(p)

def main():
    old=json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {"papers":[]}
    existing={p["id"]:p for p in old.get("papers",[]) if not p.get("is_demo")}
    rej=json.loads(REJECTED.read_text(encoding="utf-8")) if REJECTED.exists() else {}
    stems=[s.lower() for s in CFG.get("action_stems",[])]
    candidates={}
    for p in fetch_arxiv():
        p["topics"],p["relevance_score"]=classify(p)
        hay=(p["title"]+" "+p["abstract"]).lower()
        if p["relevance_score"]<CFG.get("min_relevance_score",2):continue
        if stems and not any(s in hay for s in stems) and p["relevance_score"]<CFG.get("bypass_stems_score",99):continue
        candidates[p["id"]]=p
    fresh=[p for k,p in candidates.items() if k not in existing and k not in rej]
    fresh=sorted(fresh,key=lambda p:(p["relevance_score"],p["published"]),reverse=True)[:CFG["max_new_per_run"]]
    added=0;new_batch=[]
    for p in fresh:
        p=enrich_s2(p);p["evidence"],p["evidence_note"]=evidence(p);rev=llm_review(p)
        if "relevance" in rev and (rev["relevance"]<GATE.get("min_relevance",6) or rev["rigor"]<GATE.get("min_rigor",5)):
            rej[p["id"]]={"title":p["title"],"relevance":rev["relevance"],"rigor":rev["rigor"],"reason":rev.get("reject_reason",""),"rejected_at":datetime.now(timezone.utc).date().isoformat(),"url":p["url"]}
            print(f"REJECT rel={rev['relevance']} rig={rev['rigor']} {p['title'][:60]}")
        else:
            for k in ("summary_zh","change_zh","why_it_matters"):
                if rev.get(k):p[k]=rev[k]
            if "relevance" in rev:
                p["relevance_rating"]=rev["relevance"];p["rigor_rating"]=rev["rigor"]
                if rev.get("primary_topic"):p["topics"]=[rev["primary_topic"]]
                p["venue_verified"]=venue_verified(p)
                p["score"]=rev["relevance"]*2+rev["rigor"]+(2 if p["venue_verified"] else 0)+(1 if code_signal(p) else 0)
            m=re.search(r"https?://github\.com/[\w./-]+",p["abstract"])
            if m and not p.get("code_url"):p["code_url"]=m.group(0).rstrip(".")
            p["keywords"]=sorted({w for ws in CFG["keywords"].values() for w in ws if w in (p["title"]+" "+p["abstract"]).lower()})[:8]
            p.pop("abstract",None);existing[p["id"]]=p;added+=1
            new_batch.append({"t":p["title"],"u":p["url"],"r":p.get("relevance_rating"),"g":p.get("rigor_rating"),"s":p.get("summary_zh",""),"c":p.get("code_url","")})
        time.sleep(1 if os.getenv("S2_API_KEY") else 3)
    if len(rej)>300:rej=dict(sorted(rej.items(),key=lambda kv:kv[1].get("rejected_at",""))[-300:])
    cutoff=(datetime.now(timezone.utc)-timedelta(days=CFG["retention_days"])).date().isoformat()
    papers=sorted([p for p in existing.values() if p.get("published","9999")>=cutoff],key=lambda p:(p.get("score",0),p.get("published","")),reverse=True)
    payload={"updated_at":datetime.now(timezone.utc).isoformat(),"catalog":CFG.get("topics_catalog"),"papers":papers}
    if added:payload["briefing"]={"text":write_briefing("daily",new_batch),"added":added,"at":datetime.now(timezone.utc).isoformat(),"new":new_batch}
    elif old.get("briefing"):payload["briefing"]=old["briefing"]
    DATA.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    REJECTED.write_text(json.dumps(rej,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"Added {added}; rejected {len(fresh)-added}; total {len(papers)}")
if __name__=="__main__":main()
