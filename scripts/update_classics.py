#!/usr/bin/env python3
"""Curate classic papers: last-5-years works above a citation threshold.
Discovery uses OpenAlex (no API key, generous limits) with citation-count-only
filtering — zero LLM cost. GLM writes the Chinese digest once per paper, never again."""
import json, os, re, time
from datetime import datetime, timezone
from pathlib import Path

import requests

from update_papers import CFG, DATA, UA, clean, classify, code_signal, evidence, llm_review, norm_title, venue_verified, oa_get, abstract_from_inv, arxiv_id_of
from briefing import write_briefing

ROOT=Path(__file__).resolve().parents[1]
CLASSICS=ROOT/"data/classics.json"
CC=CFG.get("classic",{})
NOW=datetime.now(timezone.utc);YEAR=NOW.year
MAXAGE=CC.get("max_age",5)

def threshold(year):return CC.get("base_citations",40)*max(1,YEAR-year)

def search():
    out={}
    for q in CC.get("queries",[]):
        hits=oa_get(f"publication_year:{YEAR-MAXAGE}-{YEAR},cited_by_count:>40,title_and_abstract.search:{q}",per_page=100)
        over=0
        for w in hits:
            y=w.get("publication_year") or 0
            if y<YEAR-MAXAGE or not w.get("display_name"):continue
            cites=w.get("cited_by_count") or 0
            if cites<threshold(y):continue
            abstract=abstract_from_inv(w.get("abstract_inverted_index"))
            if not abstract:continue
            over+=1
            aid=arxiv_id_of(w)
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

def s2_citations(ids):
    """One batched request; S2 merges preprint/published versions, so ARXIV: and DOI:
    lookups return the same merged record. Returns {s2id: (citations, arxiv_id)}."""
    try:
        r=requests.post("https://api.semanticscholar.org/graph/v1/paper/batch",params={"fields":"citationCount,externalIds"},json={"ids":ids},headers=UA,timeout=60)
        if r.status_code!=200:print(f"S2 batch status {r.status_code}; skipping S2");return {}
        body=r.json()
        rows=body.get("data") if isinstance(body,dict) else body
        out={}
        for key,x in zip(ids,rows or []):
            if x:out[key]=(x.get("citationCount") or 0,((x.get("externalIds") or {}).get("ArXiv") or ""))
        return out
    except requests.RequestException as e:
        print("S2 batch failed:",type(e).__name__);return {}

def s2_id_of(p):
    if p.get("arxiv_id"):return f"ARXIV:{p['arxiv_id']}"
    for u in (p.get("url") or "",p.get("id") or ""):
        m=re.search(r"doi\.org/(10\.[^/]+/.+)$",u)
        if m:return "DOI:"+m.group(1)
    return ""

def s2_search_citations(p):
    """Fallback for records with no arXiv id and a DOI S2 doesn't know (e.g. ACL
    proceedings DOIs): find the merged S2 record by title."""
    try:
        r=requests.get("https://api.semanticscholar.org/graph/v1/paper/search",params={"query":p["title"][:200],"limit":10,"fields":"citationCount,externalIds,title"},headers=UA,timeout=25)
        if r.status_code!=200:return 0,""
        for x in (r.json().get("data") or []):
            if norm_title(x.get("title") or "")==norm_title(p["title"]):
                return x.get("citationCount") or 0,((x.get("externalIds") or {}).get("ArXiv") or "")
    except requests.RequestException:pass
    return 0,""

def refresh_citations(old,keep):
    """Citation counts are one-time snapshots and OpenAlex splits preprint/published
    twins with separate counts — re-sync from OpenAlex (max across title-matched twins)
    plus S2 (merged versions), at most once every 5 days. Returns (ran, updated)."""
    last=old.get("last_citation_refresh") or ""
    if last:
        try:
            if (datetime.now(timezone.utc)-datetime.fromisoformat(last)).days<5:return False,0
        except ValueError:pass
    papers=list(keep.values())
    s2=s2_citations([k for k in (s2_id_of(p) for p in papers) if k])
    updated=0
    for p in papers:
        row=s2.get(s2_id_of(p)) or (0,"")
        if not row[0]:
            c,a=s2_search_citations(p)
            if c:row=(c,a or row[1])
            time.sleep(1.5)
        best=max(p.get("citation_count") or 0,row[0])
        if row[1] and not p.get("arxiv_id"):
            print(f"BACKFILL arxiv_id={row[1]} {p['title'][:55]}");p["arxiv_id"]=row[1]
        try:
            title=re.sub(r"([:,\\])",r"\\\1",p["title"])
            for w in oa_get(f"title.search:{title}",sort="cited_by_count:desc",per_page=25,tries=1) or []:
                if norm_title(w.get("display_name") or "")==norm_title(p["title"]):
                    best=max(best,w.get("cited_by_count") or 0)
        except Exception:pass
        if best>(p.get("citation_count") or 0):
            print(f"CITE {p.get('citation_count')}->{best} {p['title'][:55]}")
            p["citation_count"]=best;updated+=1
        time.sleep(1)
    print(f"Citations refreshed: {updated} updated via max(OpenAlex twins, S2) across {len(papers)} papers")
    return True,updated

def main():
    old=json.loads(CLASSICS.read_text(encoding="utf-8")) if CLASSICS.exists() else {"papers":[],"excluded":{}}
    keep={p["id"]:p for p in old.get("papers",[]) if int(str(p.get("published"))[:4] or 0)>=YEAR-MAXAGE}
    try:
        refreshed,updated=refresh_citations(old,keep)
    except Exception as e:
        print("citation refresh skipped:",type(e).__name__,str(e)[:80]);refreshed,updated=False,0
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
            p={**twin,"citation_count":c["citation_count"],"is_classic":True,"added_at":NOW.isoformat()}
            keep[p["id"]]=p;reused+=1;print(f"REUSE (0 token) {p['title'][:60]}");time.sleep(1)
            new_batch.append({"t":p["title"],"u":p["url"],"r":p.get("relevance_rating"),"g":p.get("rigor_rating"),"s":p.get("summary_zh",""),"c":p.get("code_url",""),"cit":c["citation_count"]});continue
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
            c.pop("abstract",None);c["is_classic"]=True;c["added_at"]=NOW.isoformat();keep[c["id"]]=c;added+=1
            new_batch.append({"t":c["title"],"u":c["url"],"r":c.get("relevance_rating"),"g":c.get("rigor_rating"),"s":c.get("summary_zh",""),"c":c.get("code_url",""),"cit":c["citation_count"]})
        time.sleep(1 if os.getenv("S2_API_KEY") else 3)
    if len(excl)>500:excl=dict(sorted(excl.items(),key=lambda kv:kv[1].get("excluded_at",""))[-500:])
    papers=sorted(keep.values(),key=lambda p:-p.get("citation_count",0))
    changed=(added+reused)>0 or updated>0 or len(old.get("papers",[]))!=len(papers)
    payload={"updated_at":NOW.isoformat() if changed else old.get("updated_at"),"catalog":CFG.get("topics_catalog"),"papers":papers,"excluded":excl}
    if refreshed:payload["last_citation_refresh"]=NOW.isoformat()
    if added+reused:payload["briefing"]={"text":write_briefing("classic",new_batch),"added":added+reused,"at":NOW.isoformat(),"new":new_batch}
    elif old.get("briefing"):payload["briefing"]=old["briefing"]
    CLASSICS.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"Classics: added {added} (+{reused} reused, 0 token); excluded {excluded}; total {len(papers)}; pool over threshold {len(cands)}")
    (ROOT/"data/.run_added").write_text(str(added+reused),encoding="utf-8")
if __name__=="__main__":main()
