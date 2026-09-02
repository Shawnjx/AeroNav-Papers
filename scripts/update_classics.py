#!/usr/bin/env python3
"""Curate classic papers: last-5-years works above a citation threshold.
Selection is citation-count-only (Semantic Scholar, zero LLM cost); GLM is used
once per paper to write the Chinese digest, and never again."""
import json, os, re, time
from datetime import datetime, timezone
from pathlib import Path

import requests

from update_papers import CFG, DATA, UA, clean, classify, code_signal, evidence, llm_review, norm_title, venue_verified

ROOT=Path(__file__).resolve().parents[1]
CLASSICS=ROOT/"data/classics.json"
CC=CFG.get("classic",{})
NOW=datetime.now(timezone.utc);YEAR=NOW.year
MAXAGE=CC.get("max_age",5)

def s2_get(url,params,tries=4):
    key=os.getenv("S2_API_KEY","");headers={**UA,**({"x-api-key":key} if key else {})}
    for i in range(tries):
        try:
            r=requests.get(url,params=params,headers=headers,timeout=30)
            if r.status_code==200:return r.json()
            if r.status_code==429:time.sleep(8*(i+1));continue
            return None
        except requests.RequestException:
            if i==tries-1:return None
            time.sleep(6*(i+1))
    return None

def threshold(year):return CC.get("base_citations",40)*max(1,YEAR-year)

def search():
    out={}
    for q in CC.get("queries",[]):
        d=s2_get("https://api.semanticscholar.org/graph/v1/paper/search",{"query":q,"year":f"{YEAR-MAXAGE}-{YEAR}","limit":100,"fields":"paperId,title,year,venue,citationCount,externalIds,abstract,authors,url,openAccessPdf"})
        hits=(d or {}).get("data") or []
        over=sum(1 for p in hits if p.get("year") and (p.get("citationCount") or 0)>=threshold(p["year"]))
        print(f"S2 '{q}': {len(hits)} results, {over} over threshold")
        for p in hits:
            y=p.get("year")
            if not y or y<YEAR-MAXAGE or not p.get("title") or not p.get("abstract"):continue
            if (p.get("citationCount") or 0)<threshold(y):continue
            aid=(p.get("externalIds") or {}).get("ArXiv","")
            pid=aid or f"s2:{p.get('paperId','')}"
            rec=out.setdefault(pid,{"id":pid,"arxiv_id":aid,"title":clean(p["title"]),"authors":[a.get("name","") for a in (p.get("authors") or [])[:12]],"abstract":clean(p["abstract"]),"published":str(y),"source":"Semantic Scholar","venue":clean(p.get("venue")) or "预印本","citation_count":0,"url":f"https://arxiv.org/abs/{aid}" if aid else (p.get("url") or ""),"pdf_url":(p.get("openAccessPdf") or {}).get("url") or (f"https://arxiv.org/pdf/{aid}" if aid else ""),"code_url":""})
            rec["citation_count"]=max(rec["citation_count"],p.get("citationCount") or 0)
        time.sleep(2 if os.getenv("S2_API_KEY") else 4)
    return out

def main():
    old=json.loads(CLASSICS.read_text(encoding="utf-8")) if CLASSICS.exists() else {"papers":[],"excluded":{}}
    keep={p["id"]:p for p in old.get("papers",[]) if int(str(p.get("published"))[:4] or 0)>=YEAR-MAXAGE}
    excl=dict(old.get("excluded",{}))
    daily=json.loads(DATA.read_text(encoding="utf-8")).get("papers",[]) if DATA.exists() else []
    by_aid={p["arxiv_id"]:p for p in daily if p.get("arxiv_id")}
    by_title={norm_title(p.get("title","")):p for p in daily}
    cands=search()
    fresh=[c for k,c in cands.items() if k not in keep and k not in excl]
    fresh.sort(key=lambda c:-c["citation_count"])
    added=reused=excluded=0
    for c in fresh[:CC.get("max_new_per_run",8)]:
        twin=by_aid.get(c["arxiv_id"]) or by_title.get(norm_title(c["title"]))
        if twin and twin.get("summary_zh"):
            p={**twin,"citation_count":c["citation_count"],"is_classic":True}
            keep[p["id"]]=p;reused+=1;print(f"REUSE (0 token) {p['title'][:60]}");time.sleep(1);continue
        c["topics"],_=classify(c);c["evidence"],c["evidence_note"]=evidence(c);rev=llm_review(c)
        if "relevance" in rev and (rev["relevance"]<CFG.get("gate",{}).get("min_relevance",6) or rev["rigor"]<CFG.get("gate",{}).get("min_rigor",5)):
            excl[c["id"]]={"title":c["title"],"relevance":rev["relevance"],"rigor":rev["rigor"],"reason":rev.get("reject_reason",""),"excluded_at":NOW.date().isoformat()}
            excluded+=1;print(f"EXCLUDE rel={rev['relevance']} rig={rev['rigor']} {c['title'][:60]}")
        else:
            for k in ("summary_zh","change_zh","why_it_matters"):
                if rev.get(k):c[k]=rev[k]
            if "relevance" in rev:
                c["relevance_rating"]=rev["relevance"];c["rigor_rating"]=rev["rigor"]
                if rev.get("primary_topic"):c["topics"]=[rev["primary_topic"]]
                c["venue_verified"]=venue_verified(c)
                c["score"]=rev["relevance"]*2+rev["rigor"]+(2 if c["venue_verified"] else 0)+(1 if code_signal(c) else 0)
            m=re.search(r"https?://github\.com/[\w./-]+",c["abstract"])
            if m and not c.get("code_url"):c["code_url"]=m.group(0).rstrip(".")
            c["keywords"]=sorted({w for ws in CFG["keywords"].values() for w in ws if w in (c["title"]+" "+c["abstract"]).lower()})[:8]
            c.pop("abstract",None);c["is_classic"]=True;keep[c["id"]]=c;added+=1
        time.sleep(1 if os.getenv("S2_API_KEY") else 3)
    if len(excl)>500:excl=dict(sorted(excl.items(),key=lambda kv:kv[1].get("excluded_at",""))[-500:])
    papers=sorted(keep.values(),key=lambda p:-p.get("citation_count",0))
    CLASSICS.write_text(json.dumps({"updated_at":NOW.isoformat(),"catalog":CFG.get("topics_catalog"),"papers":papers,"excluded":excl},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"Classics: added {added} (+{reused} reused, 0 token); excluded {excluded}; total {len(papers)}; pool over threshold {len(cands)}")
if __name__=="__main__":main()
