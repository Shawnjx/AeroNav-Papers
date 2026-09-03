#!/usr/bin/env python3
"""Push the latest GLM briefing to WeChat via ServerChan (sctapi).
No-op when SERVERCHAN_SENDKEY is unset or the data file has no new briefing,
so empty catch-up runs and keyless setups stay silent."""
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT=Path(__file__).resolve().parents[1]
SITE="https://shawnjx.github.io/AeroNav-Papers/"

def send(title,desp):
    key=os.getenv("SERVERCHAN_SENDKEY","")
    if not key:print("SERVERCHAN_SENDKEY unset; skip push");return False
    try:
        r=requests.post(f"https://sctapi.ftqq.com/{key}.send",data={"title":title[:32],"desp":desp},timeout=20)
        d=r.json()
    except (requests.RequestException,ValueError) as e:
        print("ServerChan failed:",type(e).__name__);return False
    print("ServerChan:",r.status_code,d.get("code"),d.get("message",""))
    return d.get("code")==0

def build_desp(b):
    """Briefing paragraph plus a one-line-per-paper digest of this run's additions (ServerChan desp limit 32KB)."""
    head=b.get("text","")
    entries=[]
    for i,p in enumerate(b.get("new") or [],1):
        meta=f"相关{p.get('r')}·严谨{p.get('g')}"+(f"，被引{p['cit']}" if p.get("cit") else "")
        links=f"[论文]({p.get('u','')})"+(f" · [代码]({p['c']})" if p.get("c") else "")
        entries.append(f"{i}. **{p['t']}**（{meta}）：{p.get('s') or ''} {links}")
    if entries:head+=f"\n\n**新增 {b.get('added',len(entries))} 篇**\n\n"+"\n\n".join(entries)
    return head

def main():
    if "--test" in sys.argv:
        send("AeroNav Papers 通道测试","简报通道已打通。之后的每日更新与经典库更新速览会从这里推送到微信。");return
    kind="classic" if "classic" in sys.argv else "daily"
    path=ROOT/("data/classics.json" if kind=="classic" else "data/papers.json")
    data=json.loads(path.read_text(encoding="utf-8"))
    b=data.get("briefing") or {}
    try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(b.get("at") or "")).total_seconds()
    except ValueError:age=-1
    if b.get("text") and 0<=age<3*3600:
        label="经典入库" if kind=="classic" else "日报"
        send(f"AeroNav {label} {b.get('at','')[:10]} · 新增{b.get('added',0)}篇",f"{build_desp(b)}\n\n---\n[打开 AeroNav Papers]({SITE})");return
    if kind=="daily" and os.getenv("EVENT_NAME")=="schedule" and " 17 " in f' {os.getenv("EVENT_SCHEDULE","")} ':
        topup="\n\n今日新增较少，已自动加跑一轮经典库更新，稍后会再推一条入库简报。" if (ROOT/"data/.run_classics_topup").exists() else ""
        send(f"AeroNav 日报 {datetime.now(timezone.utc).date()} · 今日无新增",f"今日检索线没有新论文通过质量漏斗，系统运行正常。可以翻看经典必读库或近期精选。{topup}\n\n---\n[打开 AeroNav Papers]({SITE})");return
    print("no fresh briefing; skip (catch-up or manual run)")

if __name__=="__main__":main()
