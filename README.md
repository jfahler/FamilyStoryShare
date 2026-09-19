# FamilyStoryShare

An open source tool to share family story information with a simple link and no login. Self-hosted or pushed to Cloudflare.

Turn a GEDCOM file (from Ancestry, FamilySearch, Gramps...) into a private, tap-through family story site. It builds plain static files: no login, no database, marked `noindex`, easy to host anywhere.

Needs Python 3.9+. No packages required (only `anthropic` if you use `--ai`).

## Build

```bash
python build.py sample.ged --title "The Kowalski-Nowak family" --by "Your name"
```

Open `dist/index.html` in a browser to preview.

| Option | Meaning |
|---|---|
| `gedcom` | Path to your `.ged` file (required) |
| `--out DIR` | Output folder (default `dist`) |
| `--title TEXT` | Title on the cover |
| `--by TEXT` | "Gathered by ..." credit |
| `--ai` | Draft historical context with the Anthropic API |

Photos referenced by the GEDCOM (`OBJE`/`FILE`, relative to the `.ged`) are copied into `dist/media/`. Living people (no death record, born under 100 years ago) are left out.

### Optional: AI context

```bash
pip install anthropic
set ANTHROPIC_API_KEY=your-key        # Windows cmd; use export on macOS/Linux
python build.py sample.ged --ai
```

Drafts are saved to `context.json` next to the `.ged` and labelled "AI-drafted, not yet reviewed" on the page. Read them, fix anything wrong, and set `"reviewed": true` to change the label.

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
npx wrangler pages deploy dist --project-name family-story-a8f9x
```

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

This repo uses only a fake sample. Put real GEDCOM files, photos and `context.json` in a `private/` folder, which is git-ignored:

```bash
python build.py private/mytree.ged --out dist
```

## License

MIT, see [LICENSE](LICENSE).
