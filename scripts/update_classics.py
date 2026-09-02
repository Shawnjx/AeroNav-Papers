#!/usr/bin/env python3
"""Curate classic papers: last-5-years works above a citation threshold.
Discovery uses OpenAlex (no API key, generous limits) with citation-count-only
filtering — zero LLM cost. GLM writes the Chinese digest once per paper, never again."""
import json, os, re, time
from datetime import datetime, timezone
from pathlib import Path

import requests

from update_papers import CFG, DATA, UA, clean, classify, code_signal, evidence, llm_review, norm_title, venue_verified
from briefing import write_briefing

ROOT=Path(__file__).resolve().parents[1]
CLASSICS=ROOT/"data/classics.json"
CC=CFG.get("classic",{})
NOW=datetime.now(timezone.utc);YEAR=NOW.year
MAXAGE=CC.get("max_age",5)

def oa_get(fltr,tries=3):
    for i in range(tries):
        try:
            r=requests.get("https://api.openalex.org/works",params={"filter":fltr,"sort":"cited_by_count:desc","per-page":50,"select":"display_name,publication_year,cited_by_count,ids,authorships,primary_location,best_oa_location,abstract_inverted_index","mailto":"23427669+Shawnjx@users.noreply.github.com"},headers=UA,timeout=30)
            if r.status_code==200:return r.json().get("results") or []
            if r.status_code==429:time.sleep(6*(i+1));continue
            print(f"OpenAlex status {r.status_code}");return []
        except requests.RequestException as e:
            if i==tries-1:print("OpenAlex failed:",type(e).__name__);return []
            time.sleep(6*(i+1))
    return []

def abstract_from_inv(inv):
    pos={i:w for w,idxs in (inv or {}).items() for i in idxs}
    return clean(" ".join(pos[i] for i in sorted(pos)))

def threshold(year):return CC.get("base_citations",40)*max(1,YEAR-year)

def search():
    out={}
    for q in CC.get("queries",[]):
        hits=oa_get(f"publication_year:{YEAR-MAXAGE}-{YEAR},cited_by_count:>40,title_and_abstract.search:{q}")
        over=0
        for w in hits:
            y=w.get("publication_year") or 0
            if y<YEAR-MAXAGE or not w.get("display_name"):continue
            cites=w.get("cited_by_count") or 0
            if cites<threshold(y):continue
            abstract=abstract_from_inv(w.get("abstract_inverted_index"))
            if not abstract:continue
            over+=1
            urls=" ".join(filter(None,[((w.get("best_oa_location") or {}).get("landing_page_url") or ""),((w.get("best_oa_location") or {}).get("pdf_url") or ""),(((w.get("primary_location") or {}) or {}).get("landing_page_url") or "")]))
            m=re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})",urls)
            aid=m.group(1) if m else ""
            pid=aid or (w.get("ids") or {}).get("openalex","")
            if not pid:continue
            venue=clean(((w.get("primary_location") or {}).get("source") or {}).get("display_name")) or "预印本"
            pdf=(w.get("best_oa_location") or {}).get("pdf_url") or (f"https://arxiv.org/pdf/{aid}" if aid else "")
            rec=out.setdefault(pid,{"id":pid,"arxiv_id":aid,"title":clean(w["display_name"]),"authors":[clean(a.get("author",{}).get("display_name") or "") for a in (w.get("authorships") or [])[:12]],"abstract":abstract,"published":str(y),"source":"OpenAlex","venue":venue,"citation_count":0,"url":f"https://arxiv.org/abs/{aid}" if aid else ((w.get("ids") or {}).get("doi") or (w.get("ids") or {}).get("openalex") or ""),"pdf_url":pdf,"code_url":""})
            rec["citation_count"]=max(rec["citation_count"],cites)
        print(f"OA '{q}': {len(hits)} results, {over} over threshold")
        time.sleep(1)
    if not out:print("WARNING: no candidates found at all; check OpenAlex connectivity")
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
    added=reused=excluded=0;new_batch=[]
    for c in fresh[:CC.get("max_new_per_run",8)]:
        twin=by_aid.get(c["arxiv_id"]) or by_title.get(norm_title(c["title"]))
        if twin and twin.get("summary_zh"):
            p={**twin,"citation_count":c["citation_count"],"is_classic":True}
            keep[p["id"]]=p;reused+=1;print(f"REUSE (0 token) {p['title'][:60]}");time.sleep(1)
            new_batch.append({"title":p["title"],"summary_zh":p.get("summary_zh",""),"relevance_rating":p.get("relevance_rating"),"citation_count":c["citation_count"]});continue
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
            new_batch.append({"title":c["title"],"summary_zh":c.get("summary_zh",""),"relevance_rating":c.get("relevance_rating"),"citation_count":c["citation_count"]})
        time.sleep(1 if os.getenv("S2_API_KEY") else 3)
    if len(excl)>500:excl=dict(sorted(excl.items(),key=lambda kv:kv[1].get("excluded_at",""))[-500:])
    papers=sorted(keep.values(),key=lambda p:-p.get("citation_count",0))
    payload={"updated_at":NOW.isoformat(),"catalog":CFG.get("topics_catalog"),"papers":papers,"excluded":excl}
    if added+reused:payload["briefing"]={"text":write_briefing("classic",new_batch),"added":added+reused,"at":NOW.isoformat()}
    elif old.get("briefing"):payload["briefing"]=old["briefing"]
    CLASSICS.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"Classics: added {added} (+{reused} reused, 0 token); excluded {excluded}; total {len(papers)}; pool over threshold {len(cands)}")
if __name__=="__main__":main()
