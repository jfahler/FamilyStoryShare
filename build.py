#!/usr/bin/env python3
"""Turn a curated family collection (GEDCOM + photos + documents + stories) into a
private, scrolling family story site (static HTML). See README.md for the folder layout."""
import argparse, datetime, html, json, os, re, shutil, sys
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None

LIVING_CUTOFF_YEARS = 100
MAX_PX = 1600
EVENT_LABELS = {"BIRT": "Born", "DEAT": "Died", "MARR": "Married", "IMMI": "Arrived",
                "EMIG": "Left", "RESI": "Lived", "OCCU": "Worked as", "BURI": "Buried"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}
DOC_EXT = {".pdf", ".txt"}
RASTER_EXT = {".jpg", ".jpeg", ".png"}
ID_PREFIX = re.compile(r"^([A-Za-z]+\d+)[_-]")
E = html.escape
WARNINGS = []


# ---------------------------------------------------------------- GEDCOM

def parse_gedcom(path):
    """Return (individuals, families) dicts of nodes: {tag, value, children}."""
    root = {"tag": "ROOT", "value": "", "children": [], "xref": None}
    stack = [(-1, root)]
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*(\d+)\s+(?:(@[^@]+@)\s+)?(\S+)(?:\s(.*))?$", raw)
        if not m:
            continue
        lvl, xref, tag, val = int(m[1]), m[2], m[3], (m[4] or "")
        if tag in ("CONT", "CONC") and len(stack) > 1:
            node = stack[-1][1]
            node["value"] += ("\n" if tag == "CONT" else "") + val
            continue
        node = {"tag": tag, "value": val, "children": [], "xref": xref}
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        stack[-1][1]["children"].append(node)
        stack.append((lvl, node))
    indis = {n["xref"]: n for n in root["children"] if n["tag"] == "INDI"}
    fams = {n["xref"]: n for n in root["children"] if n["tag"] == "FAM"}
    return indis, fams


def sub(node, tag):
    return next((c for c in node["children"] if c["tag"] == tag), None)


def subval(node, *tags):
    for t in tags:
        node = sub(node, t) if node else None
    return node["value"].strip() if node else ""


def year_of(date):
    m = re.search(r"\b(\d{4})\b", date or "")
    return int(m[1]) if m else None


def coord(s, neg):
    s = s.strip()
    if not s:
        return None
    try:
        v = float(s[1:])
    except ValueError:
        return None
    return -v if s[0] in neg else v


def make_event(tag, date="", place="", lat=None, lon=None, detail="", note=""):
    return {"tag": tag, "label": EVENT_LABELS[tag], "date": date, "year": year_of(date),
            "place": place, "lat": lat, "lon": lon, "detail": detail, "note": note}


def event(node, tag):
    plac = sub(node, "PLAC")
    return make_event(tag, subval(node, "DATE"), subval(node, "PLAC"),
                      coord(subval(plac, "MAP", "LATI"), "S") if plac else None,
                      coord(subval(plac, "MAP", "LONG"), "W") if plac else None,
                      node["value"].strip() if tag == "OCCU" else "", subval(node, "NOTE"))


def load_people(indis, fams, gedcom_dir):
    people = {}
    for xref, n in indis.items():
        name = re.sub(r"\s+", " ", subval(n, "NAME").replace("/", " ")).strip() or "Unknown"
        evs = [event(c, c["tag"]) for c in n["children"] if c["tag"] in EVENT_LABELS]
        media = [subval(o, "FILE") for o in n["children"] if o["tag"] == "OBJE"]
        people[xref] = {"id": xref.strip("@"), "name": name, "events": evs,
                        "media": [Path(gedcom_dir, m) for m in media if m]}
    for f in fams.values():
        m = sub(f, "MARR")
        if not m:
            continue
        ev = event(m, "MARR")
        for role in ("HUSB", "WIFE"):
            if subval(f, role) in people:
                people[subval(f, role)]["events"].append(ev)
    return people


def add_manual_people(people, manual):
    """People typed by hand into collection.json, for anyone not in a GEDCOM."""
    for m in manual:
        pid = str(m["id"])
        evs = []
        for e in m.get("events", []):
            tag = str(e.get("type", "")).upper()
            if tag not in EVENT_LABELS:
                WARNINGS.append(f"{pid}: unknown event type '{e.get('type')}' skipped")
                continue
            evs.append(make_event(tag, e.get("date", ""), e.get("place", ""), e.get("lat"),
                                  e.get("lon"), e.get("detail", ""), e.get("note", "")))
        p = {"id": pid, "name": m.get("name", "Unknown"), "events": evs, "media": []}
        if "living" in m:
            p["living_override"] = bool(m["living"])
        people[f"@{pid}@"] = p


def finalize_people(people, excluded):
    for p in people.values():
        p["events"].sort(key=lambda e: (e["year"] or 9999))
        births = [e["year"] for e in p["events"] if e["tag"] == "BIRT" and e["year"]]
        dead = any(e["tag"] == "DEAT" for e in p["events"])
        p["born"] = births[0] if births else None
        p["died"] = next((e["year"] for e in p["events"] if e["tag"] == "DEAT"), None)
        p["living"] = (not dead) and (p["born"] is None or
                                      datetime.date.today().year - p["born"] < LIVING_CUTOFF_YEARS)
        if "living_override" in p:
            p["living"] = p["living_override"]
        p["hidden"] = p["living"] or p["id"] in excluded


# ---------------------------------------------------------------- media

def collect_media(root, manifest, people):
    """Gather photos and documents from the folder, GEDCOM links and the manifest."""
    by_id = {p["id"]: p for p in people.values()}
    items = {}

    def add(path):
        path = Path(path)
        ext = path.suffix.lower()
        if ext not in IMG_EXT | DOC_EXT or not path.is_file():
            if path.suffix:
                WARNINGS.append(f"skipped {path} (missing or unsupported type)")
            return None
        return items.setdefault(path.resolve(), {
            "path": path, "kind": "photo" if ext in IMG_EXT else "document", "caption": "",
            "people": [], "year": year_of(path.stem), "private": False})

    for sub_dir in ("photos", "documents"):
        for f in sorted((root / sub_dir).rglob("*")):
            it = add(f) if f.is_file() else None
            if it:
                m = ID_PREFIX.match(f.stem)
                pid = m[1] if m and m[1] in by_id else None
                stem = f.stem[m.end():] if pid else f.stem
                it["people"] = [pid] if pid else []
                it["caption"] = re.sub(r"[_-]+", " ", stem).strip()
    for p in people.values():
        for path in p["media"]:
            it = add(path)
            if it and p["id"] not in it["people"]:
                it["people"].append(p["id"])
    for m in manifest.get("media", []):
        it = add(root / m["file"])
        if it:
            it["caption"] = m.get("caption", it["caption"])
            it["people"] = [str(x) for x in m.get("people", it["people"])]
            it["year"] = m.get("year", it["year"])
            it["private"] = bool(m.get("private", False))
    for it in items.values():
        unknown = [i for i in it["people"] if i not in by_id]
        if unknown:
            WARNINGS.append(f"{it['path'].name}: unknown person id {', '.join(unknown)}")
            it["people"] = [i for i in it["people"] if i in by_id]
    return list(items.values())


def is_visible(item, by_id):
    return not item["private"] and not any(by_id[i]["hidden"] for i in item["people"])


def copy_image(src, dst):
    """Copy a photo, downsized and stripped of EXIF (GPS, camera) when Pillow is installed."""
    ext = src.suffix.lower()
    if Image and ext in RASTER_EXT:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((MAX_PX, MAX_PX))
            if ext in (".jpg", ".jpeg") and im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(dst, quality=85)
        return
    shutil.copy(src, dst)
    if ext in (".jpg", ".jpeg"):
        msg = "Pillow not installed: JPEGs copied as-is, so GPS/camera metadata is NOT stripped (pip install Pillow)"
        if msg not in WARNINGS:
            WARNINGS.append(msg)


def publish_media(items, out):
    for n, it in enumerate(items, 1):
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", it["path"].name)
        it["url"] = f"media/{n:03d}-{safe}"
        if it["kind"] == "photo":
            copy_image(it["path"], out / it["url"])
        else:
            shutil.copy(it["path"], out / it["url"])


# ---------------------------------------------------------------- AI

def ai_context(person, cache, use_ai):
    """Cached AI-drafted historical context. Never invents family facts; needs human review."""
    key = person["id"]
    if key in cache:
        return cache[key]
    if not use_ai:
        return None
    try:
        import anthropic
    except ImportError:
        sys.exit("pip install anthropic to use --ai")
    facts = "\n".join(f"- {e['label']} {e['detail']} {e['date']} {e['place']} {e['note']}".strip()
                      for e in person["events"])
    prompt = (f"Family facts for {person['name']}:\n{facts}\n\nWrite 2-3 warm sentences of general "
              "historical context for the places and years above. Use only widely documented history. "
              "Do not add any fact about this person that is not listed. No headings.")
    msg = anthropic.Anthropic().messages.create(
        model=os.environ.get("STORY_MODEL", "claude-sonnet-5"), max_tokens=400,
        messages=[{"role": "user", "content": prompt}])
    cache[key] = {"text": msg.content[0].text.strip(), "reviewed": False}
    return cache[key]


# ---------------------------------------------------------------- HTML

def facts_html(p):
    rows = []
    for e in p["events"]:
        when = E(e["date"]) if e["date"] else ""
        what = E(" ".join(x for x in (e["detail"], e["place"]) if x))
        rows.append(f"<dt>{E(e['label'])}</dt><dd>{what}{' · ' if what and when else ''}{when}</dd>")
    return "<dl class='facts'>" + "".join(rows) + "</dl>"


def thumb(it, cls="thumb"):
    cap = it["caption"]
    return (f"<button type='button' class='{cls}' data-full='{E(it['url'])}' data-cap='{E(cap)}'>"
            f"<img loading='lazy' src='{E(it['url'])}' alt='{E(cap or 'Family photo')}'></button>")


def docs_html(docs):
    if not docs:
        return ""
    lis = "".join(f"<li><a href='{E(d['url'])}'>{E(d['caption'] or d['path'].name)}</a></li>" for d in docs)
    return f"<ul class='docs'>{lis}</ul>"


def chapter_html(i, total, p, ctx, media, story):
    span = f"{p['born'] or '?'}–{p['died'] or ''}"
    photos = [m for m in media if m["kind"] == "photo"]
    docs = [m for m in media if m["kind"] == "document"]
    hero = thumb(photos[0], "thumb hero") if photos else ""
    if photos and photos[0]["caption"]:
        hero += f"<p class='cap'>{E(photos[0]['caption'])}</p>"
    strip = ("<div class='strip'>" + "".join(thumb(m) for m in photos[1:]) + "</div>") if len(photos) > 1 else ""
    paras = [x for x in re.split(r"\n\s*\n", story.strip()) if x] if story else []
    story_block = "<div class='story'>" + "".join(f"<p>{E(x)}</p>" for x in paras) + "</div>" if paras else ""
    notes = "".join(f"<p class='note'>{E(e['note'])}</p>" for e in p["events"] if e["note"])
    ai = ""
    if ctx:
        badge = "Reviewed by the researcher" if ctx["reviewed"] else "AI-drafted, not yet reviewed"
        ai = (f"<aside class='ai'><h3>What was happening then</h3><p>{E(ctx['text'])}</p>"
              f"<small>{badge}</small></aside>")
    facts = f"<div class='card'><h3>From the family tree</h3>{facts_html(p)}{notes}</div>" if p["events"] else ""
    return (f"<section class='slide' id='c{i}'><p class='eyebrow'>Chapter {i} of {total} · {span}</p>"
            f"<h2>{E(p['name'])}</h2>{hero}{strip}{story_block}{facts}{docs_html(docs)}{ai}</section>")


def gallery_html(items):
    photos = [m for m in items if m["kind"] == "photo"]
    docs = [m for m in items if m["kind"] == "document"]
    if not items:
        return ""
    grid = "<div class='grid'>" + "".join(thumb(m) for m in photos) + "</div>" if photos else ""
    return ("<section class='slide' id='gallery'><p class='eyebrow'>The collection</p>"
            f"<h2>Photos and documents</h2>{grid}{docs_html(docs)}</section>")


def map_html(points):
    if not points:
        return ""
    items = "".join(f"<li><b>{y or ''}</b> {E(lbl)}</li>" for y, lbl, *_ in points)
    data = json.dumps([[la, lo, l] for _, l, la, lo in points]).replace("</", "<\\/")
    return ("<section class='slide' id='map'><p class='eyebrow'>The journey</p>"
            "<h2>Where they went</h2><div id='mapbox' role='img' aria-label='Map of family places'></div>"
            f"<ol class='route'>{items}</ol></section><script>window.POINTS={data}</script>")


def toc_html(chapter_count, has_gallery, has_map):
    """A sticky strip of jump links, one per section, so a scroll page still lets
    someone skip to a chapter without scrolling past everything before it."""
    items = [("cover", "Cover")] + [(f"c{i}", f"Chapter {i}") for i in range(1, chapter_count + 1)]
    if has_gallery:
        items.append(("gallery", "Photos and documents"))
    if has_map:
        items.append(("map", "The journey"))
    items.append(("end", "End"))
    links = "".join(f"<a href='#{i}' aria-label='{E(l)}'></a>" for i, l in items)
    return f"<nav class='toc' id='toc' aria-label='Jump to a section'>{links}</nav>"


CSS = """
:root{--paper:#F3ECDF;--card:#FBF7EE;--ink:#2B2118;--mut:#6B5D4E;--acc:#8C3B28;--line:#DDD0B9}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:520px;margin:0 auto;padding:24px}
h1,h2{font-family:Georgia,serif;line-height:1.1;margin:.3em 0}h1{font-size:40px}h2{font-size:32px}
h3{font-size:13px;margin:0 0 8px}.eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);font-weight:600}
.lede{color:var(--mut)}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:16px 0}
.facts{display:grid;grid-template-columns:88px 1fr;gap:6px 10px;margin:0;font-size:14px}.facts dt{color:var(--mut)}.facts dd{margin:0}
.ai{background:#E3ECE8;border-radius:12px;padding:14px 16px;color:#1F2A27}.ai h3{color:#23463F;text-transform:uppercase;letter-spacing:.08em}
.ai small{color:#3C544F}.note{font-style:italic}
.thumb{display:block;width:100%;padding:0;border:0;background:none;cursor:zoom-in}
.thumb:focus-visible{outline:3px solid var(--acc);outline-offset:2px}
.thumb img{display:block;width:100%;border-radius:12px;object-fit:cover}.hero img{max-height:320px}
.strip{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:8px 0}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:12px 0}
.strip img,.grid img{aspect-ratio:1}.cap{font-size:13px;color:var(--mut);margin:4px 0 12px}
.story p{font-family:Georgia,serif;font-size:17px;line-height:1.6}.docs{padding-left:18px}.docs a{color:var(--acc)}
#lb{position:fixed;inset:0;z-index:9;display:flex;flex-direction:column;align-items:center;justify-content:center;
padding:16px;background:rgba(20,15,10,.92);color:#F3ECDF}#lb[hidden]{display:none}
#lb img{max-width:100%;max-height:78vh;border-radius:8px}
#lbx{position:absolute;top:8px;right:8px;width:48px;height:48px;font-size:28px;border:0;border-radius:24px;
background:#F3ECDF;color:#2B2118;cursor:pointer}
#mapbox{height:280px;border-radius:12px;border:1px solid var(--line)}.route{padding-left:18px}
html{scroll-behavior:smooth}
.slide{padding:40px 0;border-bottom:1px solid var(--line);scroll-margin-top:56px}.slide:last-of-type{border-bottom:0}
.toc{position:sticky;top:0;z-index:5;display:flex;gap:10px;overflow-x:auto;padding:14px 24px;
background:rgba(243,236,223,.92);backdrop-filter:blur(6px);border-bottom:1px solid var(--line)}
.toc a{flex:0 0 auto;width:10px;height:10px;border-radius:50%;background:#CDBFA6}
.toc a:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.toc a.on{background:var(--acc);width:14px;height:14px}
#progress{display:none;position:fixed;top:0;left:0;height:3px;background:var(--acc);z-index:6;width:0}
.js #progress{display:block}
"""

JS = """
document.documentElement.classList.add('js');
const lb=document.getElementById('lb'),toc=document.getElementById('toc'),bar=document.getElementById('progress');
let map;
function loadMapIfNear(){const m=document.getElementById('map');
if(m&&window.L&&!map&&window.POINTS&&POINTS.length&&m.getBoundingClientRect().top<innerHeight+400){
map=L.map('mapbox');L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'\\u00a9 OpenStreetMap'}).addTo(map);
const pts=POINTS.map(p=>[p[0],p[1]]);pts.forEach((p,k)=>L.marker(p).addTo(map).bindPopup(POINTS[k][2]));
L.polyline(pts,{color:'#8C3B28',dashArray:'2 8'}).addTo(map);map.fitBounds(pts,{padding:[30,30]});}}
const io=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting)
toc.querySelectorAll('a').forEach(a=>a.classList.toggle('on',a.hash==='#'+e.target.id));});},
{rootMargin:'-35% 0px -55% 0px'});
document.querySelectorAll('.slide').forEach(s=>io.observe(s));
function onScroll(){const h=document.documentElement,d=h.scrollHeight-h.clientHeight;
bar.style.width=(d>0?h.scrollTop/d*100:0)+'%';loadMapIfNear();}
addEventListener('scroll',onScroll,{passive:true});onScroll();
document.addEventListener('click',e=>{const t=e.target.closest('.thumb');
if(t){const im=lb.querySelector('img');im.src=t.dataset.full;im.alt=t.dataset.cap;lb.querySelector('p').textContent=t.dataset.cap;
lb.hidden=false;document.getElementById('lbx').focus();}else if(!lb.hidden&&e.target.tagName!=='IMG')lb.hidden=true;});
addEventListener('keydown',e=>{if(!lb.hidden&&e.key==='Escape')lb.hidden=true;});
"""


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="a collection folder, or a single .ged file")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--title")
    ap.add_argument("--by")
    ap.add_argument("--ai", action="store_true", help="draft historical context via the Anthropic API")
    a = ap.parse_args()

    src = Path(a.source)
    if not src.exists():
        sys.exit(f"not found: {src}")
    if src.is_dir():
        root = src
        geds = sorted(root.glob("*.ged"))
        ged = geds[0] if geds else None
        mpath = root / "collection.json"
        manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
    else:
        root, ged, manifest = src.parent, src, {}
    title = a.title or manifest.get("title") or "Our family story"
    by = a.by or manifest.get("by") or ""

    indis, fams = parse_gedcom(ged) if ged else ({}, {})
    people = load_people(indis, fams, ged.parent if ged else root)
    add_manual_people(people, manifest.get("people", []))
    finalize_people(people, {str(x) for x in manifest.get("exclude", [])})
    by_id = {p["id"]: p for p in people.values()}

    stories = {f.stem: f.read_text(encoding="utf-8") for f in sorted((root / "stories").glob("*.txt"))}
    stories.update({str(k): v for k, v in manifest.get("stories", {}).items()})

    all_media = collect_media(root, manifest, people)
    media = [m for m in all_media if is_visible(m, by_id)]
    media.sort(key=lambda m: (m["year"] or 9999, m["path"].name))
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    (out / "media").mkdir(parents=True)
    publish_media(media, out)

    per_person = {}
    for m in media:
        for pid in m["people"]:
            per_person.setdefault(pid, []).append(m)
    told = [p for p in people.values() if not p["hidden"] and
            (len([e for e in p["events"] if e["year"]]) >= 2 or p["id"] in per_person or p["id"] in stories)]
    told.sort(key=lambda p: p["born"] or 9999)
    unattached = [m for m in media if not m["people"]]

    cache_path = root / "context.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    chapters, points = [], []
    for i, p in enumerate(told, 1):
        chapters.append(chapter_html(i, len(told), p, ai_context(p, cache, a.ai),
                                     per_person.get(p["id"], []), stories.get(p["id"], "")))
        seen = set()
        for e in p["events"]:
            if e["lat"] is not None and e["place"] not in seen:
                seen.add(e["place"])
                points.append((e["year"], f"{p['name']}: {e['place']}", e["lat"], e["lon"]))
    points.sort(key=lambda t: t[0] or 9999)
    if a.ai:
        cache_path.write_text(json.dumps(cache, indent=2))

    span = [y for p in told for y in (p["born"], p["died"]) if y]
    years = f" · {min(span)}–{max(span)}" if span else ""
    credit = f" · gathered by {E(by)}" if by else ""
    cover = (f"<section class='slide' id='cover'><p class='eyebrow'>A family story · private link</p><h1>{E(title)}</h1>"
             f"<p class='lede'>{len(told)} people{years}{credit}</p>"
             f"<p class='lede'>Scroll to begin. No login needed.</p></section>")
    end = ("<section class='slide' id='end'><h2>That's the story so far</h2>"
           "<p class='lede'>Know more? Tell the person who shared this.</p></section>")
    gallery = gallery_html(unattached)
    story_map = map_html(points)
    toc = toc_html(len(told), bool(gallery), bool(story_map))
    page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<meta name='robots' content='noindex, nofollow, noarchive'><title>{E(title)}</title>"
            f"<link rel='stylesheet' href='style.css'>"
            f"<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'></head><body>"
            f"<div id='progress'></div>{toc}<main>"
            f"{cover}{''.join(chapters)}{gallery}{story_map}{end}</main>"
            f"<div id='lb' hidden role='dialog' aria-label='Photo viewer'>"
            f"<button type='button' id='lbx' aria-label='Close'>&times;</button><img alt=''><p></p></div>"
            f"<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>"
            f"<script src='app.js'></script></body></html>")
    data = {"title": title, "by": by,
            "people": [{"id": p["id"], "name": p["name"], "born": p["born"], "died": p["died"],
                        "events": [{k: e[k] for k in ("label", "date", "place", "note")} for e in p["events"]]}
                       for p in told],
            "media": [{"url": m["url"], "kind": m["kind"], "caption": m["caption"],
                       "people": m["people"], "year": m["year"]} for m in media]}
    (out / "data.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "style.css").write_text(CSS, encoding="utf-8")
    (out / "app.js").write_text(JS, encoding="utf-8")
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n")

    hidden = sum(p["hidden"] for p in people.values())
    dropped = len(all_media) - len(media)
    print(f"Built {out}/index.html: {len(told)} chapters, "
          f"{sum(m['kind'] == 'photo' for m in media)} photos, {sum(m['kind'] == 'document' for m in media)} documents, "
          f"{len(points)} map points; {hidden} people and {dropped} files left out for privacy.")
    for w in WARNINGS:
        print("warning:", w)


if __name__ == "__main__":
    main()
