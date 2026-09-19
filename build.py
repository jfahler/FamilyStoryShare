#!/usr/bin/env python3
"""Turn a GEDCOM file into a private, tap-through family story site (static HTML)."""
import argparse, datetime, html, json, os, re, shutil, sys
from pathlib import Path

LIVING_CUTOFF_YEARS = 100
EVENT_LABELS = {"BIRT": "Born", "DEAT": "Died", "MARR": "Married", "IMMI": "Arrived",
                "EMIG": "Left", "RESI": "Lived", "OCCU": "Worked as", "BURI": "Buried"}
E = html.escape


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


def event(node, tag):
    date, place = subval(node, "DATE"), subval(node, "PLAC")
    plac = sub(node, "PLAC")
    lat = coord(subval(plac, "MAP", "LATI"), "S") if plac else None
    lon = coord(subval(plac, "MAP", "LONG"), "W") if plac else None
    detail = node["value"].strip() if tag == "OCCU" else ""
    return {"tag": tag, "label": EVENT_LABELS[tag], "date": date, "year": year_of(date),
            "place": place, "lat": lat, "lon": lon, "detail": detail, "note": subval(node, "NOTE")}


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
    for p in people.values():
        p["events"].sort(key=lambda e: (e["year"] or 9999))
        births = [e["year"] for e in p["events"] if e["tag"] == "BIRT" and e["year"]]
        dead = any(e["tag"] == "DEAT" for e in p["events"])
        p["born"] = births[0] if births else None
        p["died"] = next((e["year"] for e in p["events"] if e["tag"] == "DEAT"), None)
        p["living"] = (not dead) and (p["born"] is None or
                                      datetime.date.today().year - p["born"] < LIVING_CUTOFF_YEARS)
    return people


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


def facts_html(p):
    rows = []
    for e in p["events"]:
        when = E(e["date"]) if e["date"] else ""
        what = E(" ".join(x for x in (e["detail"], e["place"]) if x))
        rows.append(f"<dt>{E(e['label'])}</dt><dd>{what}{' · ' if what and when else ''}{when}</dd>")
    return "<dl class='facts'>" + "".join(rows) + "</dl>"


def chapter_html(i, total, p, ctx, photo):
    span = f"{p['born'] or '?'}–{p['died'] or ''}"
    notes = "".join(f"<p class='note'>{E(e['note'])}</p>" for e in p["events"] if e["note"])
    ai = ""
    if ctx:
        badge = "Reviewed by the researcher" if ctx["reviewed"] else "AI-drafted, not yet reviewed"
        ai = (f"<aside class='ai'><h3>What was happening then</h3><p>{E(ctx['text'])}</p>"
              f"<small>{badge}</small></aside>")
    img = f"<img class='photo' src='{E(photo)}' alt='Photo of {E(p['name'])}'>" if photo else ""
    return (f"<section class='slide' id='c{i}'><p class='eyebrow'>Chapter {i} of {total} · {span}</p>"
            f"<h2>{E(p['name'])}</h2>{img}<div class='card'><h3>From the family tree</h3>"
            f"{facts_html(p)}{notes}</div>{ai}</section>")


def map_html(points):
    if not points:
        return ""
    items = "".join(f"<li><b>{y or ''}</b> {E(lbl)}</li>" for y, lbl, *_ in points)
    data = json.dumps([[la, lo, l] for _, l, la, lo in points]).replace("</", "<\\/")
    return ("<section class='slide' id='map'><p class='eyebrow'>The journey</p>"
            "<h2>Where they went</h2><div id='mapbox' role='img' aria-label='Map of family places'></div>"
            f"<ol class='route'>{items}</ol></section><script>window.POINTS={data}</script>")


CSS = """
:root{--paper:#F3ECDF;--card:#FBF7EE;--ink:#2B2118;--mut:#6B5D4E;--acc:#8C3B28;--line:#DDD0B9}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:520px;margin:0 auto;padding:24px}
h1,h2{font-family:Georgia,serif;line-height:1.1;margin:.3em 0}h1{font-size:40px}h2{font-size:32px}
h3{font-size:13px;margin:0 0 8px}.eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);font-weight:600}
.lede{color:var(--mut)}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:16px 0}
.facts{display:grid;grid-template-columns:88px 1fr;gap:6px 10px;margin:0;font-size:14px}.facts dt{color:var(--mut)}.facts dd{margin:0}
.ai{background:#E3ECE8;border-radius:12px;padding:14px 16px;color:#1F2A27}.ai h3{color:#23463F;text-transform:uppercase;letter-spacing:.08em}
.ai small{color:#3C544F}.note{font-style:italic}.photo{width:100%;border-radius:12px;max-height:300px;object-fit:cover}
#mapbox{height:280px;border-radius:12px;border:1px solid var(--line)}.route{padding-left:18px}
.btn{display:block;min-height:56px;line-height:56px;text-align:center;border-radius:14px;background:var(--acc);color:#FBF7EE;
font-weight:600;font-size:17px;border:0;width:100%;cursor:pointer;padding:0}
.js .slide{display:none;min-height:80vh}.js .slide.on{display:block}
.bar{display:none;gap:6px;margin-bottom:12px}.js .bar{display:flex}.bar i{flex:1;height:4px;border-radius:2px;background:#CDBFA6}
.bar i.on{background:var(--acc)}.nav{display:none;gap:10px;margin-top:20px}.js .nav{display:flex}
.nav .back{width:56px;background:none;border:1px solid #CDBFA6;color:var(--ink)}
"""

JS = """
document.documentElement.classList.add('js');
const S=[...document.querySelectorAll('.slide')],bar=document.querySelector('.bar');
S.forEach(()=>bar.appendChild(document.createElement('i')));let n=0,map;
function show(i){n=Math.max(0,Math.min(S.length-1,i));S.forEach((s,k)=>s.classList.toggle('on',k===n));
[...bar.children].forEach((b,k)=>b.classList.toggle('on',k<=n));scrollTo(0,0);
if(S[n].id==='map'&&window.L&&!map&&window.POINTS&&POINTS.length){map=L.map('mapbox');
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'\\u00a9 OpenStreetMap'}).addTo(map);
const pts=POINTS.map(p=>[p[0],p[1]]);pts.forEach((p,k)=>L.marker(p).addTo(map).bindPopup(POINTS[k][2]));
L.polyline(pts,{color:'#8C3B28',dashArray:'2 8'}).addTo(map);map.fitBounds(pts,{padding:[30,30]});}}
document.getElementById('next').onclick=()=>show(n+1);document.getElementById('prev').onclick=()=>show(n-1);
addEventListener('keydown',e=>{if(e.key==='ArrowRight')show(n+1);if(e.key==='ArrowLeft')show(n-1)});
let x0;addEventListener('touchstart',e=>x0=e.touches[0].clientX);
addEventListener('touchend',e=>{const d=e.changedTouches[0].clientX-x0;if(Math.abs(d)>60)show(n+(d<0?1:-1))});show(0);
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("gedcom")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--title", default="Our family story")
    ap.add_argument("--by", default="")
    ap.add_argument("--ai", action="store_true", help="draft historical context via the Anthropic API")
    a = ap.parse_args()

    gdir = Path(a.gedcom).parent
    people = load_people(*parse_gedcom(a.gedcom), gdir)
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    (out / "media").mkdir(parents=True)

    told = [p for p in people.values() if not p["living"] and len([e for e in p["events"] if e["year"]]) >= 2]
    told.sort(key=lambda p: p["born"] or 9999)
    hidden = sum(p["living"] for p in people.values())

    cache_path = gdir / "context.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    chapters, points = [], []
    for i, p in enumerate(told, 1):
        photo = ""
        for m in p["media"]:
            if m.is_file():
                shutil.copy(m, out / "media" / m.name)
                photo = f"media/{m.name}"
                break
        chapters.append(chapter_html(i, len(told), p, ai_context(p, cache, a.ai), photo))
        seen = set()
        for e in p["events"]:
            if e["lat"] is not None and e["place"] not in seen:
                seen.add(e["place"])
                points.append((e["year"], f"{p['name']}: {e['place']}", e["lat"], e["lon"]))
    points.sort(key=lambda t: t[0] or 9999)
    if a.ai:
        cache_path.write_text(json.dumps(cache, indent=2))

    span = [y for p in told for y in (p["born"], p["died"]) if y]
    by = f" · gathered by {E(a.by)}" if a.by else ""
    cover = (f"<section class='slide'><p class='eyebrow'>A family story · private link</p><h1>{E(a.title)}</h1>"
             f"<p class='lede'>{len(told)} people · {min(span, default='')}–{max(span, default='')}{by}</p>"
             f"<p class='lede'>Tap next to begin. No login needed.</p></section>")
    end = ("<section class='slide'><h2>That's the story so far</h2>"
           "<p class='lede'>Know more? Tell the person who shared this.</p></section>")
    page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<meta name='robots' content='noindex, nofollow, noarchive'><title>{E(a.title)}</title>"
            f"<link rel='stylesheet' href='style.css'>"
            f"<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'></head><body><main>"
            f"<div class='bar'></div>{cover}{''.join(chapters)}{map_html(points)}{end}"
            f"<div class='nav'><button class='btn back' id='prev' aria-label='Previous'>&larr;</button>"
            f"<button class='btn' id='next'>Next</button></div></main>"
            f"<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>"
            f"<script src='app.js'></script></body></html>")
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "style.css").write_text(CSS, encoding="utf-8")
    (out / "app.js").write_text(JS, encoding="utf-8")
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    print(f"Built {out}/index.html: {len(told)} chapters, {len(points)} map points, "
          f"{hidden} living people left out.")


if __name__ == "__main__":
    main()
