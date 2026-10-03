#!/usr/bin/env python3
"""Build a static HTML report from the house-finder SQLite database."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import db

USER_AGENT = "house-finder-report/0.1"
FALLBACK_IMAGE = "https://images.unsplash.com/photo-1600607687939-ce8a6c25118c?auto=format&fit=crop&w=1200&q=85"


def image_for(listing: dict, session: requests.Session) -> str:
    supplied = listing.get("image_url", "")
    if supplied and not supplied.startswith("data:"):
        if "statics.caixaimobiliario.pt" in supplied:
            try:
                response = session.get(supplied, headers={"Referer": listing.get("url", "")}, timeout=20)
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
                return f"data:{content_type};base64,{base64.b64encode(response.content).decode('ascii')}"
            except requests.RequestException:
                return FALLBACK_IMAGE
        return supplied
    url = listing.get("url", "")
    try:
        response = session.get(url, timeout=20)
        response.raise_for_status()
    except requests.RequestException:
        return FALLBACK_IMAGE
    soup = BeautifulSoup(response.text, "html.parser")
    candidates = []
    meta = soup.select_one('meta[property="og:image"], meta[name="twitter:image"]')
    if meta and meta.get("content"):
        candidates.append(meta["content"])
    candidates.extend((image.get("data-src") or image.get("src") or "") for image in soup.select("img[src], img[data-src]"))
    for source in candidates:
        if not source or source.startswith("data:"):
            continue
        candidate = urljoin(url, source)
        lowered = candidate.lower()
        if any(word in lowered for word in ("logo", "icon", "avatar", "pixel", "placeholder", ".svg", "favicon", "banner")):
            continue
        return candidate
    return FALLBACK_IMAGE


def build(db_path: str, output_path: Path) -> None:
    db.init_db(db_path)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    with db.connect(db_path) as conn:
        auction_listings = db.fetch_listings(conn, "auction")
        market_listings = db.fetch_listings(conn, "market")
        auction_sources = db.fetch_sources(conn, "auction")
        market_sources = db.fetch_sources(conn, "market")
        discovery = db.fetch_discovery_links(conn)

        for listing in auction_listings + market_listings:
            if not listing.get("image") or "statics.caixaimobiliario.pt" in listing.get("image_url", ""):
                image = image_for(listing, session)
                db.set_listing_image(conn, listing["url"], image)
                listing["image"] = image

    generated_at = max((item.get("last_seen", "") for item in auction_listings + market_listings), default="")
    auction_source_rows = [
        {"source": s["name"], "url": s["url"], "category": s["category"], "listings": s["listings_count"] or 0, "error": s["error"] or ""}
        for s in auction_sources
    ]
    market_source_rows = [
        {"name": s["name"], "url": s["url"], "notes": s["notes"], "status": s["error"] or "reference"}
        for s in market_sources
    ]
    payload = json.dumps(
        {
            "generated_at": generated_at,
            "listings": auction_listings,
            "sources": auction_source_rows,
            "discovery": discovery,
            "market_sources": market_source_rows,
            "market_listings": market_listings,
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    output_path.write_text(TEMPLATE.replace("__DATA__", payload), encoding="utf-8")


TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lisbon Auction Ledger</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=DM+Serif+Display&display=swap');
:root{--ink:#17221d;--muted:#68736d;--paper:#f4f1e9;--panel:#fffdf8;--line:#d9ded5;--accent:#d65b3d;--olive:#5d7258;--shadow:0 18px 50px #28362b16}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:'DM Sans',sans-serif}a{color:inherit}.shell{max-width:1240px;margin:auto;padding:28px 28px 70px}.topline{display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:12px;letter-spacing:.08em;text-transform:uppercase}.mark{font-weight:700;color:var(--ink)}.hero{display:grid;grid-template-columns:1fr;gap:42px;align-items:end;padding:76px 0 56px}.eyebrow{color:var(--accent);font-weight:700;font-size:13px;letter-spacing:.14em;text-transform:uppercase}.hero h1{font-family:'DM Serif Display',serif;font-weight:400;font-size:clamp(52px,7vw,96px);line-height:.92;letter-spacing:0;margin:18px 0 22px;max-width:none}.hero p{color:var(--muted);font-size:17px;line-height:1.6;max-width:none}.hero-art{height:330px;border-radius:4px;overflow:hidden;background:linear-gradient(135deg,#c0cdbb,#e3aa74);position:relative;box-shadow:var(--shadow)}.hero-art img{width:100%;height:100%;object-fit:cover;mix-blend-mode:multiply;opacity:.88}.hero-art:after{content:'FIELD NOTES / 08.13.26';position:absolute;bottom:18px;left:18px;color:#fff;font-size:11px;letter-spacing:.15em}.stats{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin-bottom:38px}.stat{padding:22px 18px 22px 0;border-right:1px solid var(--line)}.stat:not(:first-child){padding-left:22px}.stat:last-child{border:0}.stat b{display:block;font-family:'DM Serif Display',serif;font-size:38px;font-weight:400}.stat span{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}.toolbar{display:flex;gap:12px;align-items:center;margin-bottom:24px}.toolbar input,.toolbar select{background:var(--panel);border:1px solid var(--line);padding:13px 15px;font:inherit;color:var(--ink);border-radius:2px}.toolbar input{flex:1}.toolbar select{min-width:170px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}.card{background:var(--panel);border:1px solid var(--line);box-shadow:var(--shadow);display:flex;flex-direction:column;min-width:0}.card-image{height:210px;background:#dfe6da;overflow:hidden;position:relative}.card-image img{width:100%;height:100%;object-fit:cover;transition:transform .5s}.card:hover .card-image img{transform:scale(1.04)}.tag{position:absolute;top:14px;left:14px;background:var(--ink);color:#fff;padding:7px 9px;font-size:11px;letter-spacing:.07em;text-transform:uppercase}.card-body{padding:18px}.card h2{font-family:'DM Serif Display',serif;font-size:25px;line-height:1.05;font-weight:400;margin:0 0 10px}.address{color:var(--muted);font-size:13px;min-height:35px}.price-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;border-top:1px solid var(--line);margin-top:18px;padding-top:14px}.price label{display:block;color:var(--muted);font-size:10px;letter-spacing:.08em;text-transform:uppercase;margin-bottom:5px}.price strong{font-size:15px}.not-published{color:var(--muted);font-weight:500}.card-foot{display:flex;justify-content:space-between;align-items:center;padding:13px 18px;border-top:1px solid var(--line);font-size:11px;color:var(--muted)}.visit{color:var(--accent);font-weight:700;text-decoration:none}.source-panel{margin-top:58px;padding-top:22px;border-top:1px solid var(--line)}.source-panel h2{font-family:'DM Serif Display',serif;font-size:34px;font-weight:400;margin:0 0 18px}.source-row{display:grid;grid-template-columns:1.2fr 90px 2fr;gap:20px;padding:14px 0;border-bottom:1px solid var(--line);font-size:13px}.source-row .count{font-weight:700}.source-row .error{color:var(--muted)}.empty{grid-column:1/-1;padding:40px;text-align:center;color:var(--muted);background:var(--panel)}
@media(max-width:850px){.hero{grid-template-columns:1fr;padding-top:52px}.hero-art{height:230px}.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:580px){.shell{padding:20px 16px 50px}.topline span{display:none}.hero h1{font-size:58px}.stats{grid-template-columns:repeat(2,1fr)}.stat{border-bottom:1px solid var(--line)}.toolbar{flex-wrap:wrap}.toolbar input{flex-basis:100%}.toolbar select{flex:1;min-width:0}.grid{grid-template-columns:1fr}.source-row{grid-template-columns:1fr 60px}.source-row .error{grid-column:1/-1}}
</style>
<style>.toolbar{flex-wrap:wrap}.toolbar input[type=number]{width:110px;flex:none}.toolbar select[multiple]{min-width:170px;height:78px;padding:6px 10px}.check{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--muted);white-space:nowrap}.tabs{display:flex;gap:8px;border-bottom:1px solid var(--line);margin-bottom:28px}.tab{border:0;background:transparent;color:var(--muted);font:inherit;font-weight:700;padding:13px 18px;cursor:pointer;border-bottom:2px solid transparent}.tab.active{color:var(--ink);border-color:var(--accent)}.tab-panel.hidden{display:none}.market-card{background:var(--panel);border:1px solid var(--line);padding:20px;display:flex;flex-direction:column;gap:12px}#marketGrid .market-card{display:none}.market-card h3{font-family:'DM Serif Display',serif;font-size:25px;font-weight:400;margin:0}.market-card p{color:var(--muted);font-size:13px;line-height:1.5;margin:0;flex:1}.market-card a{color:var(--accent);font-weight:700;text-decoration:none}.market-status{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--olive)}.market-status.blocked{color:var(--accent)}.dd{position:relative;min-width:170px}.dd-btn{width:100%;text-align:left;background:var(--panel);border:1px solid var(--line);padding:13px 15px;font:inherit;color:var(--ink);border-radius:2px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.dd-btn:after{content:'▾';color:var(--muted);font-size:11px}.dd-menu{position:absolute;top:calc(100% + 4px);left:0;min-width:100%;background:var(--panel);border:1px solid var(--line);border-radius:2px;box-shadow:var(--shadow);max-height:220px;overflow:auto;z-index:20;padding:6px 0}.dd-menu.hidden{display:none}.dd-menu label{display:flex;justify-content:flex-start;align-items:center;gap:8px;padding:8px 14px;font-size:13px;cursor:pointer;white-space:nowrap;text-align:left}.dd-menu label input{flex:none}.dd-menu label:hover{background:var(--paper)}.dd-menu .dd-empty{padding:8px 14px;font-size:13px;color:var(--muted)}<style>.hide-btn{border:1px solid var(--line);background:transparent;color:var(--muted);font:inherit;font-size:12px;padding:5px 10px;cursor:pointer;border-radius:2px}.hide-btn:hover{color:var(--accent);border-color:var(--accent)}.card-foot .hide-btn{margin-left:auto;margin-right:12px}</style>
</head>
<body>
<main class="shell">
<header class="topline"><span class="mark">House Finder / Lisbon</span><span>Public auction intelligence</span></header>
<nav class="tabs"><button class="tab active" data-tab="market">Market homes</button><button class="tab" data-tab="auctions">Auctions</button></nav>
<section id="auctionsPanel" class="tab-panel hidden">
<section class="hero"><div><div class="eyebrow">District report · 13 August 2026</div><h1>Homes under the hammer.</h1><p>A live snapshot of publicly visible residential auction records around Lisbon. Prices are shown exactly as published; missing bid values are never guessed.</p></div></section>
<section class="stats"><div class="stat"><b id="listingCount">0</b><span>Listings found</span></div><div class="stat"><b id="sourceCount">0</b><span>Sources checked</span></div><div class="stat"><b id="bidCount">0</b><span>Bids published</span></div><div class="stat"><b id="coverage">0%</b><span>Source coverage</span></div></section>
<section class="toolbar"><input id="search" type="search" placeholder="Search title, address or concelho"><select id="source"><option value="">All sources</option></select><div class="dd" id="municipality" data-empty="All concelhos"><button type="button" class="dd-btn" aria-haspopup="listbox" aria-label="Filter by concelho">All concelhos</button><div class="dd-menu hidden" role="listbox"></div></div><div class="dd" id="freguesia" data-empty="All freguesias"><button type="button" class="dd-btn" aria-haspopup="listbox" aria-label="Filter by freguesia">All freguesias</button><div class="dd-menu hidden" role="listbox"></div></div><select id="typology" multiple size="3" aria-label="Filter by typology"><option value="">All typologies</option></select><input id="minPrice" type="number" min="0" placeholder="Min €"><input id="maxPrice" type="number" min="0" placeholder="Max €"><label class="check"><input id="hasBid" type="checkbox"> Bid published</label><select id="sort"><option value="source">Sort by source</option><option value="price">Sort by published price</option><option value="title">Sort by title</option></select></section>
<section id="grid" class="grid"></section>
<section class="source-panel"><h2>Source ledger</h2><div id="sources"></div></section>
<section class="source-panel"><h2>Discovered source links <span id="discoveryCount"></span></h2><div id="discovery"></div></section>
</section>
<section id="marketPanel" class="tab-panel"><section class="hero"><div><div class="eyebrow">Lisbon district · ordinary sales</div><h1>Homes on the market.</h1><p>Active ordinary-sale listings gathered nightly from Portuguese property portals. Newly posted homes can be isolated with the toggle below.</p></div></section>
<section class="stats"><div class="stat"><b id="marketActiveCount">0</b><span>Active homes</span></div><div class="stat"><b id="marketNewCount">0</b><span>Newly posted</span></div><div class="stat"><b id="marketSourceCount">0</b><span>Sources crawled</span></div><div class="stat"><b id="marketCoverage">0%</b><span>Source coverage</span></div></section>
<div class="source-panel"><section class="toolbar"><input id="marketSearch" type="search" placeholder="Search title, address or concelho"><select id="marketSource"><option value="">All sources</option></select><div class="dd" id="marketMunicipality" data-empty="All concelhos"><button type="button" class="dd-btn" aria-haspopup="listbox" aria-label="Filter by concelho">All concelhos</button><div class="dd-menu hidden" role="listbox"></div></div><div class="dd" id="marketFreguesia" data-empty="All freguesias"><button type="button" class="dd-btn" aria-haspopup="listbox" aria-label="Filter by freguesia">All freguesias</button><div class="dd-menu hidden" role="listbox"></div></div><select id="marketTypology" aria-label="Minimum typology"><option value="">Any typology</option><option value="0">T0+</option><option value="1">T1+</option><option value="2" selected>T2+</option><option value="3">T3+</option><option value="4">T4+</option><option value="5">T5+</option><option value="6">T6+</option></select><input id="marketMinPrice" type="number" min="0" placeholder="Min €" value="10000"><input id="marketMaxPrice" type="number" min="0" placeholder="Max €" value="450000"><input id="marketMinArea" type="number" min="0" placeholder="Min área bruta (m²)" value="70"><select id="marketSort"><option value="newest">Newest first</option><option value="price" selected>Sort by price</option><option value="title">Sort by title</option><option value="source">Sort by source</option></select><label class="check"><input id="marketNewOnly" type="checkbox"> Only Newly Posted Houses</label><label class="check"><input id="marketHiddenOnly" type="checkbox"> Hidden only (<span id="marketHiddenCount">0</span>)</label></section><div id="marketGrid" class="grid"></div><div class="market-status-panel"><h2>Market source results</h2><h3>Crawled successfully</h3><div id="marketSuccessful"></div><div id="marketUnsuccessful"></div></div></div></section>
</main>
<script type="application/json" id="report-data">__DATA__</script>
<script>
const data=JSON.parse(document.getElementById('report-data').textContent), listings=data.listings||[], sources=data.sources||[], discovery=data.discovery||[], marketSources=data.market_sources||[], marketListings=data.market_listings||[];
const marketCutoff=Date.now()-30*24*60*60*1000;marketListings.splice(0,marketListings.length,...marketListings.filter(x=>{let stamp=x.published_at||x.removed_at||x.first_seen||x.last_seen;return !stamp||Date.parse(stamp)>=marketCutoff}));
const euro=v=>v==null?'<span class="not-published">Not published</span>':new Intl.NumberFormat('pt-PT',{style:'currency',currency:'EUR',maximumFractionDigits:0}).format(v);
const esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function ddUpdateLabel(dd){let checked=[...dd.querySelectorAll('input:checked')],btn=dd.querySelector('.dd-btn');btn.textContent=checked.length?(checked.length===1?checked[0].value:checked.length+' selected'):dd.dataset.empty;}
function ddPopulate(id,values,onChange){let dd=document.getElementById(id),menu=dd.querySelector('.dd-menu'),btn=dd.querySelector('.dd-btn');btn.onclick=()=>{let open=!menu.classList.contains('hidden');document.querySelectorAll('.dd-menu').forEach(m=>m.classList.add('hidden'));menu.classList.toggle('hidden',open);};menu.innerHTML=values.length?values.map(v=>`<label><input type="checkbox" value="${esc(v)}">${esc(v)}</label>`).join(''):'<div class="dd-empty">No options</div>';menu.querySelectorAll('input').forEach(cb=>cb.addEventListener('change',()=>{ddUpdateLabel(dd);onChange();}));ddUpdateLabel(dd);}
function ddValues(id){return [...document.querySelectorAll('#'+id+' input:checked')].map(i=>i.value);}
document.addEventListener('click',e=>{if(!e.target.closest('.dd'))document.querySelectorAll('.dd-menu').forEach(m=>m.classList.add('hidden'));});
document.getElementById('listingCount').textContent=listings.length;document.getElementById('sourceCount').textContent=sources.length;document.getElementById('bidCount').textContent=listings.filter(x=>x.current_bid_eur!=null||x.minimum_bid_eur!=null).length;document.getElementById('coverage').textContent=Math.round(sources.filter(x=>!x.error).length/sources.length*100)+'%';
document.getElementById('discoveryCount').textContent='('+discovery.length+' links)';
const sourceSelect=document.getElementById('source');[...new Set(listings.map(x=>x.source))].forEach(s=>sourceSelect.insertAdjacentHTML('beforeend',`<option>${esc(s)}</option>`));
ddPopulate('municipality',[...new Set(listings.map(x=>x.municipality).filter(Boolean))].sort(),render);
const marketSourceSelect=document.getElementById('marketSource');[...new Set(marketListings.filter(x=>x.is_active!==0).map(x=>x.source))].forEach(s=>marketSourceSelect.insertAdjacentHTML('beforeend',`<option>${esc(s)}</option>`));
ddPopulate('marketMunicipality',[...new Set(marketListings.filter(x=>x.is_active!==0).map(x=>x.municipality).filter(Boolean))].sort(),renderMarket);
const locationParts=x=>String(x.address||'').split(',').map(part=>part.trim()).filter(Boolean);
const cleanedLocationParts=x=>locationParts(x).filter(part=>part.toLowerCase()!=='portugal'&&!/^\d{4}-\d{3}$/.test(part));
const concelhoOf=x=>x.municipality||((locationParts(x).length>=2)?locationParts(x).at(-2):'');
const freguesiaOf=x=>x.freguesia||(()=>{let parts=cleanedLocationParts(x), last=(parts.at(-1)||'').toLowerCase(), municipality=(x.municipality||'').toLowerCase();return parts.length>=3&&(last==='lisboa'||last===municipality)?parts.at(-3):''})();
const typologyOf=x=>x.typology||((String(x.title||'').match(/\bT[0-6]\b/i)||[])[0]||'').toUpperCase();
ddPopulate('freguesia',[...new Set(listings.map(freguesiaOf).filter(Boolean))].sort(),render);
ddPopulate('marketFreguesia',[...new Set(marketListings.filter(x=>x.is_active!==0).map(freguesiaOf).filter(Boolean))].sort(),renderMarket);
const typologySelect=document.getElementById('typology');[...new Set(listings.map(typologyOf).filter(Boolean))].sort().forEach(t=>typologySelect.insertAdjacentHTML('beforeend',`<option value="${esc(t)}">${esc(t)}</option>`));
const marketTypologySelect=document.getElementById('marketTypology');
const typologyMinOf=x=>{let m=typologyOf(x).match(/T(\d+)/i);return m?parseInt(m[1],10):null};
const latestMarketFirstSeen=marketListings.reduce((max,item)=>item.first_seen>max?item.first_seen:max,'');
const isNewListing=x=>x.is_active!==0&&!!x.first_seen&&x.first_seen===latestMarketFirstSeen;
const newMarketCount=marketListings.filter(isNewListing).length;
function render(){let q=document.getElementById('search').value.toLowerCase(), source=sourceSelect.value, concelhos=ddValues('municipality'), freguesias=ddValues('freguesia'), typologies=[...typologySelect.selectedOptions].map(option=>option.value).filter(Boolean), sort=document.getElementById('sort').value, minPrice=parseFloat(document.getElementById('minPrice').value), maxPrice=parseFloat(document.getElementById('maxPrice').value), hasBid=document.getElementById('hasBid').checked;let rows=listings.filter(x=>{let price=x.current_bid_eur??x.minimum_bid_eur??x.published_price_eur;return (!q||[x.title,x.address,concelhoOf(x),x.source].join(' ').toLowerCase().includes(q))&&(!source||x.source===source)&&(!concelhos.length||concelhos.includes(concelhoOf(x)))&&(!freguesias.length||freguesias.includes(freguesiaOf(x)))&&(!typologies.length||typologies.includes(typologyOf(x)))&&(!hasBid||x.current_bid_eur!=null||x.minimum_bid_eur!=null)&&(isNaN(minPrice)||(price!=null&&price>=minPrice))&&(isNaN(maxPrice)||(price!=null&&price<=maxPrice))});rows.sort((a,b)=>sort==='title'?a.title.localeCompare(b.title):sort==='price'?((a.published_price_eur??Infinity)-(b.published_price_eur??Infinity)):a.source.localeCompare(b.source));document.getElementById('grid').innerHTML=rows.length?rows.map(x=>`<article class="card"><div class="card-image"><img loading="lazy" src="${esc(x.image)}" alt="${esc(x.title)}"><span class="tag">${esc(x.source)}</span></div><div class="card-body"><h2>${esc(x.title)}</h2><div class="address">${esc(x.address||concelhoOf(x))}</div><div class="price-row"><div class="price"><label>Minimum bid</label><strong>${euro(x.minimum_bid_eur)}</strong></div><div class="price"><label>Current bid</label><strong>${euro(x.current_bid_eur)}</strong></div></div></div><div class="card-foot"><span>${x.published_price_eur!=null?'Published: '+euro(x.published_price_eur):'No bid published'}</span><a class="visit" href="${esc(x.url)}" target="_blank" rel="noreferrer">Open listing ↗</a></div></article>`).join(''):'<div class="empty">No listings match this search.</div>'}
function renderSources(){document.getElementById('sources').innerHTML=sources.map(x=>`<div class="source-row"><strong>${esc(x.source)}</strong><span class="count">${x.listings||0}</span><span class="error">${x.error?esc(x.error):'Crawled successfully'}</span></div>`).join('')}
function renderDiscovery(){document.getElementById('discovery').innerHTML=discovery.length?discovery.map(x=>`<div class="source-row"><strong>${esc(x.source||'Source')}</strong><span class="count">↗</span><a class="error" href="${esc(x.url)}" target="_blank" rel="noreferrer">${esc(x.text||x.url)}</a></div>`).join(''):'<div class="empty">No discovery links found.</div>'}
let hiddenSet=new Set();try{hiddenSet=new Set(JSON.parse(localStorage.getItem('hf_hidden')||'[]'))}catch(e){}
const updateHiddenCount=()=>{document.getElementById('marketHiddenCount').textContent=hiddenSet.size};updateHiddenCount();
document.getElementById('marketGrid').addEventListener('click',e=>{let b=e.target.closest('.hide-btn');if(!b)return;let u=b.dataset.url;hiddenSet.has(u)?hiddenSet.delete(u):hiddenSet.add(u);try{localStorage.setItem('hf_hidden',JSON.stringify([...hiddenSet]))}catch(err){}updateHiddenCount();renderMarket()});
function renderMarket(){let q=document.getElementById('marketSearch').value.toLowerCase(), source=marketSourceSelect.value, concelhos=ddValues('marketMunicipality'), freguesias=ddValues('marketFreguesia'), typologyMin=document.getElementById('marketTypology').value, sort=document.getElementById('marketSort').value, maxPrice=parseFloat(document.getElementById('marketMaxPrice').value), minPrice=parseFloat(document.getElementById('marketMinPrice').value), newOnly=document.getElementById('marketNewOnly').checked, hiddenOnly=document.getElementById('marketHiddenOnly').checked, minArea=parseFloat(document.getElementById('marketMinArea').value);let rows=marketListings.filter(x=>{let price=x.published_price_eur;return (hiddenOnly?hiddenSet.has(x.url):!hiddenSet.has(x.url))&&(!newOnly||isNewListing(x))&&(isNaN(minPrice)||(price!=null&&price>=minPrice))&&x.is_active!==0&&(!q||[x.title,x.address,concelhoOf(x),x.source].join(' ').toLowerCase().includes(q))&&(!source||x.source===source)&&(!concelhos.length||concelhos.includes(concelhoOf(x)))&&(!freguesias.length||freguesias.includes(freguesiaOf(x)))&&(typologyMin===''||(typologyMinOf(x)!==null&&typologyMinOf(x)>=parseInt(typologyMin,10)))&&(isNaN(maxPrice)||(price!=null&&price<=maxPrice))&&(isNaN(minArea)||(x.area_m2!=null&&x.area_m2>=minArea))});rows.sort((a,b)=>{if(sort==='title')return a.title.localeCompare(b.title);if(sort==='price')return (a.published_price_eur??Infinity)-(b.published_price_eur??Infinity);if(sort==='source')return a.source.localeCompare(b.source);let aHas=!!a.published_at, bHas=!!b.published_at;if(aHas!==bHas)return aHas?-1:1;return String(b.published_at||b.last_seen||'').localeCompare(String(a.published_at||a.last_seen||''));});let cards=rows.map(x=>`<article class="card"><div class="card-image"><img loading="lazy" src="${esc(x.image)}" alt="${esc(x.title)}"><span class="tag">${esc(x.source)}</span></div><div class="card-body"><h2>${esc(x.title)}</h2><div class="address">${esc(x.address||concelhoOf(x))}</div><div class="price-row"><div class="price"><label>Published price</label><strong>${euro(x.published_price_eur)}</strong></div><div class="price"><label>Published</label><strong>${esc((x.published_at||'').slice(0,10)||'Not published')}</strong></div></div><div class="price-row"><div class="price"><label>Tipologia</label><strong>${esc(typologyOf(x)||'—')}</strong></div><div class="price"><label>Área bruta</label><strong>${x.area_m2?esc(x.area_m2+' m²'):'—'}</strong></div></div></div><div class="card-foot"><span>Ordinary sale</span><button type="button" class="hide-btn" data-url="${esc(x.url)}">${hiddenOnly?'Restore':'Hide'}</button><a class="visit" href="${esc(x.url)}" target="_blank" rel="noreferrer">Open listing ↗</a></div></article>`);let portals=marketSources.map(x=>`<article class="market-card"><span class="market-status ${x.status==='blocked'?'blocked':''}">${esc(x.status||'reference')}</span><h3>${esc(x.name||x.source)}</h3><p>${esc(x.notes||x.error||'Market source')}</p><a href="${esc(x.url||'#')}" target="_blank" rel="noreferrer">Open Lisbon search ↗</a></article>`);document.getElementById('marketGrid').innerHTML=(cards.length?cards:['<div class="empty">No listings match this search.</div>']).concat(portals).join('')}
function renderMarketStatus(){let successful=marketSources.filter(x=>!x.error), unsuccessful=marketSources.filter(x=>x.error);let row=x=>`<div class="source-row"><strong>${esc(x.name||x.source)}</strong><span class="count">${x.listings||0}</span><span class="error">${esc(x.error||'Crawled successfully')}</span></div>`;document.getElementById('marketSuccessful').innerHTML=successful.length?successful.map(row).join(''):'<div class="empty">No market sources completed successfully.</div>';document.getElementById('marketUnsuccessful').innerHTML=unsuccessful.length?'<h3>Not crawled successfully</h3>'+unsuccessful.map(row).join(''):'<div class="empty">All market sources completed successfully.</div>'}
function renderMarketChanges(){let panel=document.querySelector('.market-status-panel'), today=Date.now()-24*60*60*1000, current=marketListings.filter(x=>x.is_active!==0), removed=marketListings.filter(x=>x.is_active===0), newListings=marketListings.filter(x=>x.first_seen&&Date.parse(x.first_seen)>=today);let section=document.createElement('section');section.className='market-change-summary';section.innerHTML=`<h2>Listing changes</h2><p>${newListings.length} new this crawl window; ${current.length} currently active in the 30-day window; ${removed.length} no longer listed.</p>`;panel.prepend(section)}
document.querySelectorAll('.tab').forEach(tab=>tab.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));tab.classList.add('active');document.getElementById('auctionsPanel').classList.toggle('hidden',tab.dataset.tab!=='auctions');document.getElementById('marketPanel').classList.toggle('hidden',tab.dataset.tab!=='market')}));
['search','source','typology','sort','minPrice','maxPrice','hasBid'].forEach(id=>['input','change'].forEach(event=>document.getElementById(id).addEventListener(event,render)));
['marketSearch','marketSource','marketTypology','marketSort','marketMaxPrice','marketMinPrice','marketMinArea','marketNewOnly','marketHiddenOnly'].forEach(id=>['input','change'].forEach(event=>document.getElementById(id).addEventListener(event,renderMarket)));
{let dd=document.getElementById('marketMunicipality'),cb=[...dd.querySelectorAll('input')].find(i=>i.value==='Lisboa');if(cb){cb.checked=true;ddUpdateLabel(dd)}}
{let active=marketListings.filter(x=>x.is_active!==0),ok=marketSources.filter(x=>!x.error).length;document.getElementById('marketActiveCount').textContent=active.length;document.getElementById('marketNewCount').textContent=newMarketCount;document.getElementById('marketSourceCount').textContent=marketSources.length;document.getElementById('marketCoverage').textContent=marketSources.length?Math.round(ok/marketSources.length*100)+'%':'0%';}
render();renderSources();renderMarket();renderMarketStatus();renderMarketChanges();
renderDiscovery();
</script>
</body></html>'''

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="path to the SQLite database")
    parser.add_argument("--output", default="index.html")
    args = parser.parse_args()
    build(args.db, Path(args.output))
    print(f"Wrote {args.output}")
