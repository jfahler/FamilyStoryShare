# FamilyStoryShare

An open source tool to share family story information with a simple link and no login. Self-hosted or pushed to Cloudflare.

Turn a GEDCOM file (from Ancestry, FamilySearch, Gramps...) into a private, scrolling family story site. It builds plain static files: no login, no database, marked `noindex`, easy to host anywhere.

Needs Python 3.9+. No packages are required. Optional: `Pillow` (shrinks photos and strips GPS/camera data) and `anthropic` (for `--ai`).

## Curate a collection

Instead of relying on outside sites, gather exactly what you want to share into one folder. Everything in it is bundled into the site.

```
my-family/
  tree.ged                  optional GEDCOM export (Ancestry, FamilySearch, Gramps...)
  collection.json           optional: title, credit, extra people, captions, privacy
  photos/                   your family photos
  documents/                letters, records (.pdf or .txt)
  stories/I1.txt            your own words about person I1
```

**Attach a file to a person** by starting its name with their ID and `_` or `-`, for example `photos/I1_anna-portrait-1907.jpg`. The rest of the name becomes the caption. Files with no ID go into a shared "Photos and documents" page. You can also attach and caption files in `collection.json`.

**`collection.json`** (all keys optional):

```json
{
  "title": "The Kowalski-Nowak family",
  "by": "Your name",
  "people": [
    {"id": "M1", "name": "Zofia Kowalski",
     "events": [{"type": "BIRT", "date": "1892", "place": "Kraków, Poland"}]}
  ],
  "media": [
    {"file": "photos/dinner.jpg", "caption": "Sunday dinner, 1952", "year": 1952, "people": ["I3"]},
    {"file": "photos/private-one.jpg", "private": true}
  ],
  "stories": {"I1": "Text shown on Anna's chapter."},
  "exclude": ["I9"]
}
```

Event types: `BIRT`, `DEAT`, `MARR`, `IMMI`, `EMIG`, `RESI`, `OCCU`, `BURI`. No GEDCOM at all? List people in `collection.json` and it still works.

## Build

```bash
python build.py sample
```

Open `dist/index.html` in a browser to preview. You can also pass a single `.ged` file instead of a folder.

| Option | Meaning |
|---|---|
| `source` | A collection folder, or a single `.ged` file (required) |
| `--out DIR` | Output folder (default `dist`) |
| `--title TEXT` | Title on the cover (overrides `collection.json`) |
| `--by TEXT` | "Gathered by ..." credit (overrides `collection.json`) |
| `--ai` | Draft historical context with the Anthropic API |

The build also writes `dist/data.json`, the curated data behind the site.

**Privacy rules:** living people (no death record, born under 100 years ago) are left out, along with any photo tagged to them. Files marked `"private": true` and people in `exclude` are left out too. Photos with no person attached are your call, so check them. Install Pillow to strip GPS and camera details from JPEGs; the build warns if it isn't installed.

### Optional: AI context

```bash
pip install anthropic
set ANTHROPIC_API_KEY=your-key        # Windows cmd; use export on macOS/Linux
python build.py sample --ai
```

Drafts are saved to `context.json` in the collection folder and labelled "AI-drafted, not yet reviewed" on the page. Read them, fix anything wrong, and set `"reviewed": true` to change the label.

## Publish

The site is only as private as its link. Use a hard-to-guess name and share the link directly. Anyone with the link can view it.

Make a random suffix:

```bash
python -c "import secrets; print(secrets.token_hex(4))"
```

### Cloudflare Pages (recommended)

One-time: install [Node.js](https://nodejs.org), then log in.

```bash
npx wrangler login
```

Deploy (creates `https://<name>.pages.dev` on first run, re-run to update):

```bash
npx wrangler pages deploy dist --project-name family-story-a8f9x --branch production
```

Keep `--branch production` on every deploy. Any other branch name is published as a separate preview URL and the main address won't update.

### GitHub Pages

Use a separate repo for the output so your data and source stay apart. Free GitHub Pages requires a public repo, so anyone could find the files if they find the repo.

```bash
gh repo create family-story-a8f9x --public
cd dist
git init -b main
git add .
git commit -m "Publish family story"
git remote add origin https://github.com/<you>/family-story-a8f9x.git
git push -u origin main
gh api -X POST repos/<you>/family-story-a8f9x/pages -f "source[branch]=main" -f "source[path]=/"
```

The site appears at `https://<you>.github.io/family-story-a8f9x/`.

## Keep real family data private

This repo uses only a fake sample. Put your real collection folder inside `private/`, which is git-ignored:

```bash
python build.py private/my-family --out dist
```

## License

MIT, see [LICENSE](LICENSE).
