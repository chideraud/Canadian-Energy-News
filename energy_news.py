"""
Canadian Energy Law Brief: builds a phone-friendly news page each day.

Searches Google News for Canadian energy-law stories from the past six
months, keeps a running archive in docs/articles.json, and writes
docs/index.html as one list grouped by month, newest first.
GitHub Pages serves the docs folder as your website.

Run locally:
    pip install feedparser
    python energy_news.py          # then open docs/index.html

Optional email copy of this month's stories (only sent if these are set):
    DIGEST_EMAIL_FROM, DIGEST_EMAIL_TO, DIGEST_EMAIL_PASS
    DIGEST_SMTP_HOST (defaults to smtp.gmail.com)
"""

import html
import json
import os
import re
import smtplib
import socket
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser

socket.setdefaulttimeout(20)  # skip any feed that takes over 20 seconds

# ---------------------------------------------------------------------------
# 1. What to watch — edit these freely
# ---------------------------------------------------------------------------

# Words that make a story about ENERGY. Every story must contain at least one.
ENERGY_WORDS = [
    "energy", "oil", "gas", "pipeline", "lng", "oil sands", "crude",
    "electricity", "hydro", "power plant", "power project", "grid",
    "utility", "utilities", "nuclear", "smr", "uranium", "renewable",
    "solar", "wind farm", "wind project", "wind power", "hydrogen",
    "carbon", "emissions", "battery storage",
    # Major companies and projects, for headlines that only name them
    "trans mountain", "coastal gaslink", "enbridge", "tc energy",
    "suncor", "cenovus", "canadian natural", "imperial oil", "pembina",
    "keyera", "tourmaline", "arc resources", "ovintiv", "fortis", "emera",
    "opg", "bruce power", "brookfield renewable", "hydro one", "ieso", "aeso",
]

# Each section has its own searches, extra feeds, and LEGAL words.
# A story is kept only if it has an energy word AND one of its section's legal words.
SECTIONS = {
    "Deals & law firms": {
        "searches": [
            # Big Canadian firms with major energy practices
            '"Osler" OR "Blakes" OR "McCarthy Tetrault" energy',
            '"Torys" OR "Stikeman Elliott" OR "Davies Ward" energy',
            '"Bennett Jones" OR "Burnet Duckworth" OR "Norton Rose Fulbright" energy',
            '"Dentons" OR "Fasken" OR "Gowling" OR "Borden Ladner" energy',
            # Legal trade press
            "site:canadianlawyermag.com energy",
            "site:lexpert.ca energy",
            "site:law360.ca energy",
            # Deals that may not name the firm in the headline
            "Canada energy acquisition counsel advised",
            "Canada energy merger acquisition billion",
        ],
        "feeds": [
            "https://www.cbc.ca/webfeed/rss/rss-business",
        ],
        "legal_words": [
            "osler", "blakes", "blake, cassels", "mccarthy", "torys",
            "stikeman", "davies", "bennett jones", "burnet", "norton rose",
            "dentons", "fasken", "gowling", "borden ladner", "blg",
            "law firm", "lawyer", "counsel", "advis", "acquisition", "acquire",
            "merger", "takeover", "deal", "transaction", "financing",
        ],
    },
    "Courts & litigation": {
        "searches": [
            "Canada court ruling pipeline OR energy",
            '"Supreme Court of Canada" energy OR pipeline OR oil',
            '"Federal Court of Appeal" energy OR pipeline',
            "duty to consult First Nation energy project court",
            "Canada energy lawsuit OR class action",
            "Canada energy arbitration",
        ],
        "feeds": [],
        "legal_words": [
            "court", "judge", "ruling", "appeal", "lawsuit", "sued", "sues",
            "litigation", "class action", "injunction", "arbitration",
            "tribunal", "duty to consult", "charter", "constitutional",
        ],
    },
    "Regulators & decisions": {
        "searches": [
            '"Canada Energy Regulator" decision OR approval OR hearing',
            '"Alberta Energy Regulator" OR "Alberta Utilities Commission"',
            '"Ontario Energy Board" decision OR ruling',
            '"BC Utilities Commission" OR "BC Energy Regulator"',
            '"Canadian Nuclear Safety Commission" licence',
            '"Impact Assessment Agency" project decision',
        ],
        "feeds": [],
        "legal_words": [
            "regulator", "commission", "energy board", "tribunal", "decision",
            "ruling", "approv", "reject", "hearing", "licence", "license",
            "permit", "application", "rate case", "impact assessment",
        ],
    },
    "Legislation & policy": {
        "searches": [
            '"Impact Assessment Act" Canada',
            '"Clean Electricity Regulations" Canada',
            "Canada oil gas emissions cap regulations",
            "Canada energy bill legislation Parliament",
            "Alberta OR Ontario OR BC energy legislation bill",
            "Canada carbon pricing law regulation",
        ],
        "feeds": [],
        "legal_words": [
            "act", "bill", "legislation", "regulation", "law", "amendment",
            "parliament", "legislature", "royal assent", "ottawa",
            "federal government", "minister", "policy",
        ],
    },
}

KEEP_DAYS = 183           # stories older than about six months are dropped
LOCAL_TZ = ZoneInfo("America/Toronto")
SITE_TITLE = "Canadian Energy Law Brief"
OUT_DIR = Path(__file__).parent / "docs"
ARCHIVE = OUT_DIR / "articles.json"

# ---------------------------------------------------------------------------
# 2. Fetch and filter
# ---------------------------------------------------------------------------

def search_windows(now: datetime) -> list[tuple[str, str]]:
    """Date ranges to search separately, so older months aren't crowded out.
    Each Google News search returns at most ~100 stories."""
    edges = [0, 30, 90, KEEP_DAYS]
    out = []
    for newer, older in zip(edges, edges[1:]):
        after = (now - timedelta(days=older)).date().isoformat()
        before = (now - timedelta(days=newer) + timedelta(days=1)).date().isoformat()
        out.append((after, before))
    return out


def google_news_feed(query: str, after: str, before: str) -> str:
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(f"{query} after:{after} before:{before}")
        + "&hl=en-CA&gl=CA&ceid=CA:en"
    )


def fetch(url: str) -> list:
    """Read one feed. If it's slow or broken, skip it instead of crashing."""
    try:
        return feedparser.parse(url).entries
    except Exception as err:
        print(f"Skipped a feed ({type(err).__name__}): {url[:90]}")
        return []


def has_word(text: str, words) -> bool:
    # Match at the start of a word, so "act" doesn't match "contract".
    return any(re.search(r"\b" + re.escape(w), text) for w in words)


def matches(entry, legal_words) -> bool:
    text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
    return has_word(text, ENERGY_WORDS) and has_word(text, legal_words)


def clean_title(title: str, source: str) -> str:
    # Google News appends " - Source Name" to titles; we show the source separately.
    suffix = f" - {source}"
    if source and title.endswith(suffix):
        return title[: -len(suffix)]
    return title


def published_at(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*t[:6], tzinfo=timezone.utc) if t else None


def load_archive() -> list[dict]:
    try:
        items = json.loads(ARCHIVE.read_text())
        for a in items:
            a["published"] = datetime.fromisoformat(a["published"])
        return items
    except (FileNotFoundError, ValueError, KeyError):
        return []


def save_archive(items: list[dict]) -> None:
    data = [{**a, "published": a["published"].isoformat()} for a in items]
    ARCHIVE.write_text(json.dumps(data, indent=1))


def collect(now: datetime) -> list[dict]:
    """Return every kept story from the past six months, newest first.
    Each story has: section, title, link, source, published."""
    cutoff = now - timedelta(days=KEEP_DAYS)
    items = [a for a in load_archive() if a["published"] >= cutoff]
    seen_links = {a["link"] for a in items}
    seen_titles = {a["title"].lower() for a in items}
    windows = search_windows(now)

    # Sections are checked in order, so a story goes to the first one it fits.
    for name, cfg in SECTIONS.items():
        urls = [google_news_feed(q, a, b) for q in cfg["searches"] for a, b in windows]
        urls += cfg["feeds"]
        for url in urls:
            for entry in fetch(url):
                link = entry.get("link")
                source = entry.get("source", {}).get("title", "")
                title = clean_title(entry.get("title", "").strip(), source)
                published = published_at(entry)
                if not link or not published or published < cutoff:
                    continue
                if link in seen_links or title.lower() in seen_titles:
                    continue
                if not matches(entry, cfg["legal_words"]):
                    continue
                items.append({"section": name, "title": title, "link": link,
                              "source": source, "published": published})
                seen_links.add(link)
                seen_titles.add(title.lower())
            time.sleep(0.5)  # be polite to Google News

    items = [a for a in items if a["section"] in SECTIONS]
    items.sort(key=lambda a: a["published"], reverse=True)
    save_archive(items)
    return items


def in_this_month(published: datetime, now: datetime) -> bool:
    start = now.astimezone(LOCAL_TZ).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return published >= start

# ---------------------------------------------------------------------------
# 3. Build the page
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<meta name="description" content="Canadian energy law headlines from the past six months.">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon-192.png">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Energy Law">
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
    padding: calc(env(safe-area-inset-top) + 14px) 16px 12px;
    border-bottom: 1px solid var(--line);
  }}
  h1 {{ margin: 0; font-size: 22px; letter-spacing: -0.01em; }}
  .updated {{ margin: 2px 0 12px; color: var(--muted); font-size: 13px; }}
  .filters {{ display: flex; gap: 8px; overflow-x: auto; margin: 0 -16px; padding: 0 16px;
             scrollbar-width: none; }}
  .filters::-webkit-scrollbar {{ display: none; }}
  .filters button {{
    flex: none; border: 1px solid var(--line); border-radius: 999px;
    padding: 6px 12px; font: 600 14px/1.2 inherit; font-family: inherit;
    background: var(--card); color: var(--muted); cursor: pointer;
  }}
  .filters button[aria-pressed="true"] {{
    background: var(--accent-soft); border-color: transparent; color: var(--accent);
  }}
  .filters .n {{ font-weight: 500; opacity: .75; }}
  main {{ max-width: 680px; margin: 0 auto; padding: 4px 16px calc(env(safe-area-inset-bottom) + 32px); }}
  h2 {{
    font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); margin: 22px 0 8px;
  }}
  h2 span {{ color: var(--accent); }}
  .month[hidden], li[hidden] {{ display: none; }}
  ul {{ list-style: none; margin: 0; padding: 0; background: var(--card);
       border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }}
  li + li {{ border-top: 1px solid var(--line); }}
  li.first-visible {{ border-top: 0; }}
  li a {{ display: block; padding: 12px 14px; color: inherit; text-decoration: none; }}
  li a:active {{ background: var(--accent-soft); }}
  .tag {{ display: inline-block; font-size: 11px; font-weight: 700; letter-spacing: .04em;
         text-transform: uppercase; color: var(--accent); margin-bottom: 3px; }}
  .headline {{ font-weight: 600; }}
  .meta {{ margin-top: 3px; font-size: 13px; color: var(--muted); }}
  .empty {{ margin-top: 24px; padding: 14px; color: var(--muted); text-align: center; }}
  footer {{ margin-top: 28px; font-size: 12px; color: var(--muted); text-align: center; }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <p class="updated">Updated {updated}</p>
  <div class="filters">{filters}</div>
</header>
<main>
{months}
<p class="empty" id="none" hidden>No stories for this topic in the past six months.</p>
<footer>Headlines link to the original publishers. Refreshed every morning.</footer>
</main>
<script>
  // Topic filters: show only one topic's stories, or all of them.
  const buttons = document.querySelectorAll(".filters button");
  function pick(topic) {{
    buttons.forEach(b => b.setAttribute("aria-pressed", b.dataset.topic === topic));
    let any = false;
    document.querySelectorAll(".month").forEach(m => {{
      let shown = 0;
      m.querySelectorAll("li").forEach(li => {{
        const show = topic === "all" || li.dataset.topic === topic;
        li.hidden = !show;
        li.classList.toggle("first-visible", show && shown === 0);
        if (show) shown++;
      }});
      m.hidden = shown === 0;
      m.querySelector("h2 span").textContent = shown;
      if (shown) any = true;
    }});
    document.getElementById("none").hidden = any;
    try {{ localStorage.setItem("topic", topic); }} catch (e) {{}}
  }}
  buttons.forEach(b => b.addEventListener("click", () => {{ pick(b.dataset.topic); window.scrollTo(0, 0); }}));
  let saved = null;
  try {{ saved = localStorage.getItem("topic"); }} catch (e) {{}}
  if (saved && document.querySelector('.filters button[data-topic="' + saved + '"]')) pick(saved);

  // Recent stories show "3h ago"; older ones keep their date.
  const now = Date.now();
  document.querySelectorAll("time[datetime]").forEach(el => {{
    const mins = Math.round((now - Date.parse(el.dateTime)) / 60000);
    if (isNaN(mins) || mins < 0 || mins >= 7 * 1440) return;
    el.textContent = mins < 60 ? mins + "m ago"
      : mins < 1440 ? Math.round(mins / 60) + "h ago"
      : Math.round(mins / 1440) + "d ago";
  }});
</script>
</body>
</html>
"""


def fmt_date(dt: datetime) -> str:
    local = dt.astimezone(LOCAL_TZ)
    return f"{local:%b} {local.day}"


def fmt_updated(dt: datetime) -> str:
    local = dt.astimezone(LOCAL_TZ)
    hour = local.strftime("%I:%M %p").lstrip("0").lower()
    return f"{local:%a, %b} {local.day}, {hour}"


def slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def build_page(items: list[dict], now: datetime) -> str:
    esc = html.escape

    # Topic filter buttons
    filters = [f'<button data-topic="all" aria-pressed="true">All <span class="n">{len(items)}</span></button>']
    for name in SECTIONS:
        n = sum(1 for a in items if a["section"] == name)
        filters.append(f'<button data-topic="{slug(name)}" aria-pressed="false">'
                       f'{esc(name)} <span class="n">{n}</span></button>')

    # One block per month, newest first (items are already sorted newest first)
    by_month: dict[str, list[dict]] = {}
    for a in items:
        key = a["published"].astimezone(LOCAL_TZ).strftime("%B %Y")
        by_month.setdefault(key, []).append(a)

    months = []
    for label, group in by_month.items():
        rows = []
        for a in group:
            meta = [esc(a["source"])] if a["source"] else []
            meta.append(f'<time datetime="{a["published"].isoformat()}">'
                        f'{esc(fmt_date(a["published"]))}</time>')
            rows.append(
                f'<li data-topic="{slug(a["section"])}">'
                f'<a href="{esc(a["link"])}" target="_blank" rel="noopener">'
                f'<div class="tag">{esc(a["section"])}</div>'
                f'<div class="headline">{esc(a["title"])}</div>'
                f'<div class="meta">{" · ".join(meta)}</div></a></li>'
            )
        months.append(f'<section class="month"><h2>{esc(label)} <span>{len(group)}</span></h2>'
                      f'<ul>\n' + "\n".join(rows) + "\n</ul></section>")
    if not months:
        months.append('<p class="empty">No stories yet. Check back after the next update.</p>')

    return PAGE.format(
        title=esc(SITE_TITLE),
        updated=esc(fmt_updated(now)),
        filters="".join(filters),
        months="\n".join(months),
    )

# ---------------------------------------------------------------------------
# 4. Optional email copy (this month's stories)
# ---------------------------------------------------------------------------

def build_email(items: list[dict], now: datetime) -> str:
    month = [a for a in items if in_this_month(a["published"], now)]
    lines = [f"{SITE_TITLE}: {now.astimezone(LOCAL_TZ):%B %Y} so far", ""]
    for name in SECTIONS:
        group = [a for a in month if a["section"] == name]
        lines += [name.upper(), "-" * len(name)]
        if not group:
            lines.append("Nothing yet this month.")
        for a in group:
            src = f" ({a['source']})" if a["source"] else ""
            lines.append(f"• {a['title']}{src}, {fmt_date(a['published'])}\n  {a['link']}")
        lines.append("")
    return "\n".join(lines)


def send_email(body: str, now: datetime) -> None:
    sender = os.environ["DIGEST_EMAIL_FROM"]
    msg = MIMEText(body)
    msg["Subject"] = f"{SITE_TITLE}: {now.astimezone(LOCAL_TZ):%b %d}"
    msg["From"] = sender
    msg["To"] = os.environ["DIGEST_EMAIL_TO"]
    host = os.environ.get("DIGEST_SMTP_HOST", "smtp.gmail.com")
    with smtplib.SMTP_SSL(host, 465) as server:
        server.login(sender, os.environ["DIGEST_EMAIL_PASS"])
        server.send_message(msg)


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    OUT_DIR.mkdir(exist_ok=True)
    items = collect(now)
    (OUT_DIR / "index.html").write_text(build_page(items, now), encoding="utf-8")
    this_month = sum(in_this_month(a["published"], now) for a in items)
    print(f"Built docs/index.html: {len(items)} stories, {this_month} this month.")

    if os.environ.get("DIGEST_EMAIL_TO"):
        send_email(build_email(items, now), now)
        print("Email sent.")
