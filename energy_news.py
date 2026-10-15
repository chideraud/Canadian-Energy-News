"""
Canadian Energy Brief: builds a phone-friendly news page each day.

Pulls the past day's headlines from Google News and other RSS feeds,
sorts them into sections, and writes docs/index.html. GitHub Pages
serves that folder as your website, which you can save to your phone's
home screen like an app.

Run locally:
    pip install -r requirements.txt
    python energy_news.py          # then open docs/index.html

Optional email copy (only sent if these are set; Gmail needs an App Password):
    DIGEST_EMAIL_FROM, DIGEST_EMAIL_TO, DIGEST_EMAIL_PASS
    DIGEST_SMTP_HOST (defaults to smtp.gmail.com)
"""

import html
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser

# ---------------------------------------------------------------------------
# 1. What to watch — edit these freely
# ---------------------------------------------------------------------------

# Each section has its own searches, extra feeds, and keyword filter.
# An article is kept only if its title or summary contains a keyword.
SECTIONS = {
    "Energy news": {
        "searches": [
            "Canada oil gas",
            "Canada pipeline",
            "Alberta oil sands",
            "Canada Energy Regulator",
            "Ontario electricity OR Hydro One OR IESO",
            "Canada renewable energy OR wind OR solar",
            "Canada nuclear SMR",
            "Canada LNG",
        ],
        "feeds": [
            "https://www.cbc.ca/webfeed/rss/rss-business",
        ],
        "keywords": [
            "energy", "oil", "gas", "pipeline", "lng", "oil sands", "crude",
            "electricity", "hydro", "power grid", "nuclear", "smr",
            "renewable", "solar", "wind", "carbon", "emissions",
            # Major companies and projects, for headlines that only name them
            "trans mountain", "coastal gaslink", "enbridge", "tc energy",
            "suncor", "cenovus", "canadian natural", "imperial oil",
            "pembina", "keyera", "tourmaline", "arc resources", "ovintiv",
            "fortis", "emera", "opg", "bruce power", "brookfield renewable",
            "ieso", "aeso",
        ],
    },
    "Energy law & deals": {
        "searches": [
            # Big Canadian firms with major energy practices
            '"Osler" OR "Blakes" OR "McCarthy Tetrault" energy deal',
            '"Torys" OR "Stikeman Elliott" OR "Davies Ward" energy deal',
            '"Bennett Jones" OR "Burnet Duckworth" OR "Norton Rose Fulbright" energy',
            '"Dentons" OR "Fasken" OR "Gowling" energy transaction Canada',
            # Deal coverage that may not name the firm in the headline
            "Canada energy acquisition legal counsel advised",
            "Canada energy merger billion",
        ],
        "feeds": [],
        "keywords": [
            "osler", "blakes", "blake, cassels", "mccarthy", "torys",
            "stikeman", "davies", "bennett jones", "burnet", "norton rose",
            "dentons", "fasken", "gowling", "advis", "counsel", "law firm",
            "acquisition", "acquire", "merger", "deal", "transaction",
        ],
    },
}

MAX_PER_SECTION = 25
LOCAL_TZ = ZoneInfo("America/Toronto")
SITE_TITLE = "Canadian Energy Brief"
OUT_DIR = Path(__file__).parent / "docs"

# ---------------------------------------------------------------------------
# 2. Fetch and filter
# ---------------------------------------------------------------------------

def google_news_feed(query: str) -> str:
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(query + " when:1d")
        + "&hl=en-CA&gl=CA&ceid=CA:en"
    )


def matches(entry, keywords) -> bool:
    text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
    return any(k in text for k in keywords)


def clean_title(title: str, source: str) -> str:
    # Google News appends " - Source Name" to titles; we show the source separately.
    suffix = f" - {source}"
    if source and title.endswith(suffix):
        return title[: -len(suffix)]
    return title


def published_at(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*t[:6], tzinfo=timezone.utc) if t else None


def collect() -> dict[str, list[dict]]:
    seen_links, seen_titles = set(), set()
    results = {}

    for name, cfg in SECTIONS.items():
        feeds = [google_news_feed(q) for q in cfg["searches"]] + cfg["feeds"]
        items = []
        for url in feeds:
            for entry in feedparser.parse(url).entries:
                link = entry.get("link")
                source = entry.get("source", {}).get("title", "")
                title = clean_title(entry.get("title", "").strip(), source)
                if not link or link in seen_links or title.lower() in seen_titles:
                    continue
                if not matches(entry, cfg["keywords"]):
                    continue
                items.append({
                    "title": title,
                    "link": link,
                    "source": source,
                    "published": published_at(entry),
                })
                seen_links.add(link)
                seen_titles.add(title.lower())

        oldest = datetime.min.replace(tzinfo=timezone.utc)
        items.sort(key=lambda a: a["published"] or oldest, reverse=True)
        results[name] = items[:MAX_PER_SECTION]

    return results

# ---------------------------------------------------------------------------
# 3. Build the page
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<meta name="description" content="Daily Canadian energy and energy-law headlines.">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon-192.png">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Energy Brief">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="theme-color" content="#f6f4ef" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#14171a" media="(prefers-color-scheme: dark)">
<style>
  :root {{
    --bg: #f6f4ef; --card: #ffffff; --text: #1d2124; --muted: #6b7075;
    --line: #e4e0d8; --accent: #c2410c; --accent-soft: #fbe9df;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #14171a; --card: #1d2125; --text: #ecebe8; --muted: #9a9fa5;
      --line: #2c3136; --accent: #fb923c; --accent-soft: #3a2518;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font: 16px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    -webkit-text-size-adjust: 100%;
  }}
  header {{
    position: sticky; top: 0; z-index: 2; background: var(--bg);
    padding: calc(env(safe-area-inset-top) + 14px) 16px 10px;
    border-bottom: 1px solid var(--line);
  }}
  h1 {{ margin: 0; font-size: 22px; letter-spacing: -0.01em; }}
  .updated {{ margin: 2px 0 10px; color: var(--muted); font-size: 13px; }}
  nav {{ display: flex; gap: 8px; overflow-x: auto; }}
  nav a {{
    flex: none; padding: 6px 12px; border-radius: 999px; font-size: 14px;
    background: var(--accent-soft); color: var(--accent); text-decoration: none;
    font-weight: 600;
  }}
  main {{ max-width: 680px; margin: 0 auto; padding: 8px 16px calc(env(safe-area-inset-bottom) + 32px); }}
  section {{ scroll-margin-top: 120px; }}
  h2 {{
    font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); margin: 22px 0 8px;
  }}
  h2 span {{ color: var(--accent); }}
  ul {{ list-style: none; margin: 0; padding: 0; background: var(--card);
       border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }}
  li + li {{ border-top: 1px solid var(--line); }}
  li a {{ display: block; padding: 12px 14px; color: inherit; text-decoration: none; }}
  li a:active {{ background: var(--accent-soft); }}
  .headline {{ font-weight: 600; }}
  .meta {{ margin-top: 3px; font-size: 13px; color: var(--muted); }}
  .empty {{ padding: 14px; color: var(--muted); }}
  footer {{ margin-top: 28px; font-size: 12px; color: var(--muted); text-align: center; }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <p class="updated">Updated {updated}</p>
  <nav>{nav}</nav>
</header>
<main>
{sections}
<footer>Headlines link to the original publishers. Refreshed every morning.</footer>
</main>
<script>
  // Show "3h ago" style times, worked out on your phone when you open the page.
  const now = Date.now();
  document.querySelectorAll("time[datetime]").forEach(el => {{
    const mins = Math.round((now - Date.parse(el.dateTime)) / 60000);
    if (isNaN(mins) || mins < 0) return;
    el.textContent = mins < 60 ? mins + "m ago"
      : mins < 1440 ? Math.round(mins / 60) + "h ago"
      : Math.round(mins / 1440) + "d ago";
  }});
</script>
</body>
</html>
"""


def slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def fmt_local(dt: datetime) -> str:
    local = dt.astimezone(LOCAL_TZ)
    hour = local.strftime("%I:%M %p").lstrip("0").lower()
    return f"{local:%a, %b} {local.day}, {hour}"


def build_page(results) -> str:
    esc = html.escape
    nav, sections = [], []
    for name, items in results.items():
        sid = slug(name)
        nav.append(f'<a href="#{sid}">{esc(name)}</a>')
        rows = []
        for a in items:
            meta = []
            if a["source"]:
                meta.append(esc(a["source"]))
            if a["published"]:
                meta.append(
                    f'<time datetime="{a["published"].isoformat()}">'
                    f'{esc(fmt_local(a["published"]))}</time>'
                )
            rows.append(
                f'<li><a href="{esc(a["link"])}" target="_blank" rel="noopener">'
                f'<div class="headline">{esc(a["title"])}</div>'
                f'<div class="meta">{" · ".join(meta)}</div></a></li>'
            )
        body = "\n".join(rows) or '<li class="empty">Nothing new today.</li>'
        sections.append(
            f'<section id="{sid}"><h2>{esc(name)} <span>{len(items)}</span></h2>'
            f"<ul>\n{body}\n</ul></section>"
        )

    return PAGE.format(
        title=esc(SITE_TITLE),
        updated=esc(fmt_local(datetime.now(timezone.utc))),
        nav="".join(nav),
        sections="\n".join(sections),
    )

# ---------------------------------------------------------------------------
# 4. Optional email copy
# ---------------------------------------------------------------------------

def build_email(results) -> str:
    lines = [f"{SITE_TITLE}, {datetime.now(LOCAL_TZ):%A, %B %d}", ""]
    for name, items in results.items():
        lines += [name.upper(), "-" * len(name)]
        if not items:
            lines.append("Nothing new today.")
        for a in items:
            src = f" ({a['source']})" if a["source"] else ""
            lines.append(f"• {a['title']}{src}\n  {a['link']}")
        lines.append("")
    return "\n".join(lines)


def send_email(body: str) -> None:
    sender = os.environ["DIGEST_EMAIL_FROM"]
    msg = MIMEText(body)
    msg["Subject"] = f"{SITE_TITLE}: {datetime.now(LOCAL_TZ):%b %d}"
    msg["From"] = sender
    msg["To"] = os.environ["DIGEST_EMAIL_TO"]
    host = os.environ.get("DIGEST_SMTP_HOST", "smtp.gmail.com")
    with smtplib.SMTP_SSL(host, 465) as server:
        server.login(sender, os.environ["DIGEST_EMAIL_PASS"])
        server.send_message(msg)


if __name__ == "__main__":
    results = collect()
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "index.html").write_text(build_page(results), encoding="utf-8")
    total = sum(len(v) for v in results.values())
    print(f"Built docs/index.html with {total} headlines.")

    if os.environ.get("DIGEST_EMAIL_TO"):
        send_email(build_email(results))
        print("Email sent.")
