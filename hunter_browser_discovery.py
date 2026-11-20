#!/usr/bin/env python3
"""HUNTER's Playwright global product-directory research.

Searches public web results in multiple languages. Output is untrusted candidate
metadata consumed by hunter_discovery.py; it never submits forms or grants permission.
"""
from __future__ import annotations
import argparse, json, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlparse, urlunparse

ENGINES={
 "duckduckgo":"https://html.duckduckgo.com/html/?q={q}",
 "bing":"https://www.bing.com/search?q={q}",
 "mojeek":"https://www.mojeek.com/search?q={q}",
}
QUERIES=[
 "free product directory submit your tool product official submission rules",
 "submit a digital product directory free listing terms",
 "notion template directory submit product listing guidelines",
 "startup software directory submit product free official rules",
 "submit tool directory product listing API allowed",
 "submit SaaS app directory free listing guidelines",
 "AI tool directory submit product free rules",
 "indie product directory submit your startup listing",
 "directorio de productos enviar producto gratis reglas",
 "annuaire produit soumettre un outil gratuit règles",
 "Produktverzeichnis Produkt einreichen kostenlos Richtlinien",
 "diretório de produtos enviar ferramenta gratuita regras",
 "catalogo prodotti inviare strumento gratuito regole",
 "каталог продуктов добавить продукт правила",
 "ürün dizini ürün gönder ücretsiz kurallar",
 "دليل المنتجات إرسال منتج قواعد النشر",
 "उत्पाद निर्देशिका उत्पाद जमा करने के नियम",
 "katalog produk kirim produk gratis aturan",
 "製品ディレクトリ 製品登録 無料 利用規約",
 "제품 디렉토리 제품 제출 무료 운영 정책",
 "产品目录 提交产品 免费 收录规则",
 "danh bạ sản phẩm gửi công cụ miễn phí quy tắc",
 "katalog produktów dodaj produkt darmowe zasady",
 "แคตตาล็อกผลิตภัณฑ์ ส่งผลิตภัณฑ์ฟรี กฎ",
 "каталог інструментів додати продукт безкоштовно правила",
]
BLOCK=("captcha","verify you are human","unusual traffic","access denied","security check","robot check","sign in to continue","forbidden")
EXCLUDE=("facebook.com","instagram.com","x.com","twitter.com","reddit.com","linkedin.com","youtube.com","t.me","discord.com","pinterest.com","medium.com")

def clean(raw):
 try:
  p=urlparse(raw); host=(p.hostname or "").lower().rstrip(".")
  if p.scheme!="https" or not host or p.username or p.password or p.port not in (None,443):return ""
  if any(host==x or host.endswith("."+x) for x in (*EXCLUDE,"duckduckgo.com","bing.com","mojeek.com")):return ""
  return urlunparse(("https",p.netloc,p.path or "/", "", "", ""))
 except Exception:return ""

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--out",type=Path,required=True);ap.add_argument("--max-pages",type=int,default=24);args=ap.parse_args()
 try:from playwright.sync_api import sync_playwright
 except Exception as e:raise SystemExit("Playwright unavailable: "+type(e).__name__)
 leads={};errors=[];pages=0;seen=set()
 page_limit=max(1,min(args.max_pages,60))
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True);page=browser.new_page(locale="en-US")
  page.set_default_timeout(12000)
  for query in QUERIES:
   for engine,template in ENGINES.items():
    if pages>=page_limit:break
    try:
     response=page.goto(template.format(q=quote_plus(query)),wait_until="domcontentloaded",timeout=12000);pages+=1
     try: body=page.locator("body").inner_text(timeout=2500).lower()
     except Exception: body=""
     if (response and response.status in (401,403,429)) or any(x in body for x in BLOCK):
      errors.append(engine+":challenge_or_rate_limit");continue
     items=page.locator("a[href]").evaluate_all("els=>els.slice(0,300).map(a=>({url:a.href,title:(a.innerText||a.getAttribute('aria-label')||'').trim()}))")
     for item in items[:300]:
      if not isinstance(item,dict):continue
      url=clean(item.get("url",""));title=re.sub(r"\s+"," ",str(item.get("title",""))).strip()
      if not url or url in seen or len(title)<3:continue
      seen.add(url)
      hay=(title+" "+url).lower()
      if not any(w in hay for w in ("directory","submit","product","tool","startup","saas","app","software","listing","catalog","catalogue","resource","verzeichnis","annuaire","directorio","catalogo","目录","製品","제품","каталог","dizin","دليل","निर्देशिका","katalog","danh bạ","แคตตาล็อก")):continue
      leads[url]={"url":url,"name":title[:160],"source":"playwright:"+engine+":"+query[:180],"catalog_free":"Unknown"}
    except Exception as e:errors.append(engine+":"+type(e).__name__)
   if pages>=page_limit:break
  browser.close()
 args.out.parent.mkdir(parents=True,exist_ok=True)
 args.out.write_text(json.dumps(list(leads.values())[:2000],indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
 print(json.dumps({"mode":"research_only","queries":len(QUERIES),"search_pages":pages,"new_candidate_seeds":len(leads),"errors":errors[-30:]}))
if __name__=="__main__":main()
