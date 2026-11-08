# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools>=4.50", "brotli>=1.1"]
# ///
"""Build the SVGs used by profile/README.md.

    uv run scripts/build_assets.py            # fetch fresh numbers with `gh`, then draw
    uv run scripts/build_assets.py --offline  # redraw from scripts/.cache/

Writes profile/assets/{banner,activity}-{light,dark}.svg. The fonts (Fraunces and
Instrument Sans, both SIL OFL) are downloaded once, cut to static weights, subset to
the glyphs each SVG uses and embedded as WOFF2, because an SVG shown through <img>
may not load anything from outside itself.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "scripts" / ".cache"
OUT = ROOT / "profile" / "assets"

START = "2025-01"  # TopTop Oy's GitHub organisation was created in January 2025.

# Every product repository, and the series it is drawn in.
REPOS = {
    "TopTop-Oy/bp": "busypipe",
    "TopTop-Oy/family-vault": "vault",
    "TopTop-Oy/mdfi": "mdfi",
    "TopTop-Oy/tenderia": "other",
    "TopTop-Oy/DDDesktop": "other",
    "DiyazY/toptop-landing-page": "other",
    "DiyazY/gambling": "other",
    "DiyazY/j-project": "other",
    "DiyazY/toptop-scandicity": "other",
}
SERIES = [
    ("busypipe", "busypipe"),
    ("vault", "Family Vault"),
    ("mdfi", "mdfi.ai"),
    ("other", "Salpön, Eurojackpot Stats, toptop.dev and more"),
]

FONT_URLS = {
    "Fraunces.ttf": "https://github.com/google/fonts/raw/main/ofl/fraunces/Fraunces%5BSOFT,WONK,opsz,wght%5D.ttf",
    "InstrumentSans.ttf": "https://github.com/google/fonts/raw/main/ofl/instrumentsans/InstrumentSans%5Bwdth,wght%5D.ttf",
}


# --------------------------------------------------------------------------- theme

@dataclass(frozen=True)
class Theme:
    name: str
    paper: str
    ink: str
    body: str
    muted: str
    rings: str
    grid: str
    handle: str
    berry: str
    birch: str
    grain: str
    series: dict[str, str]


LIGHT = Theme(
    name="light", paper="#F3EEE4", ink="#2A2522", body="#4A423A", muted="#5E554C",
    rings="#E2D6C0", grid="#DDD2BE", handle="#2A2522", berry="#B23A3A",
    birch="#D9B98C", grain="#B08A5A",
    series={"busypipe": "#B23A3A", "vault": "#8A6A42", "mdfi": "#D9B98C", "other": "#C9BCA4"},
)
DARK = Theme(
    name="dark", paper="#1C1916", ink="#F1EAE0", body="#CFC5B6", muted="#B3A797",
    rings="#2E2924", grid="#3A332D", handle="#F1EAE0", berry="#C94545",
    birch="#D9B98C", grain="#B08A5A",
    series={"busypipe": "#E8786C", "vault": "#D9B98C", "mdfi": "#9C7A4E", "other": "#5A5048"},
)


# --------------------------------------------------------------------------- fonts

@dataclass
class Face:
    family: str
    font: TTFont

    def width(self, text: str, size: float) -> float:
        cmap = self.font.getBestCmap()
        hmtx = self.font["hmtx"]
        upm = self.font["head"].unitsPerEm
        return sum(hmtx[cmap[ord(c)]][0] for c in text if ord(c) in cmap) * size / upm

    def css(self, text: str) -> str:
        options = subset.Options()
        options.flavor = "woff2"
        options.layout_features = ["kern", "liga", "lnum", "tnum"]
        sub = subset.Subsetter(options)
        sub.populate(text=text)
        font = TTFont(io.BytesIO(self._bytes()))
        sub.subset(font)
        buf = io.BytesIO()
        font.flavor = "woff2"
        font.save(buf)
        data = base64.b64encode(buf.getvalue()).decode()
        return f"@font-face{{font-family:'{self.family}';src:url(data:font/woff2;base64,{data}) format('woff2');}}"

    def _bytes(self) -> bytes:
        buf = io.BytesIO()
        self.font.save(buf)
        return buf.getvalue()


def load_faces() -> dict[str, Face]:
    fonts_dir = CACHE / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for name, url in FONT_URLS.items():
        path = fonts_dir / name
        if not path.exists():
            urllib.request.urlretrieve(url, path)

    def cut(file: str, family: str, axes: dict[str, float]) -> Face:
        return Face(family, instancer.instantiateVariableFont(TTFont(fonts_dir / file), axes))

    return {
        "display": cut("Fraunces.ttf", "TT Display", {"wght": 800, "opsz": 144, "SOFT": 0, "WONK": 0}),
        "serif": cut("Fraunces.ttf", "TT Serif", {"wght": 500, "opsz": 48, "SOFT": 0, "WONK": 0}),
        "sans": cut("InstrumentSans.ttf", "TT Sans", {"wght": 400, "wdth": 100}),
        "sans-bold": cut("InstrumentSans.ttf", "TT Sans Bold", {"wght": 600, "wdth": 100}),
    }


class Text:
    """Collects the strings drawn in each face, so each SVG embeds only the glyphs it uses."""

    def __init__(self, faces: dict[str, Face]):
        self.faces = faces
        self.used: dict[str, set[str]] = defaultdict(set)

    def __call__(self, face: str, s: str, x: float, y: float, size: float, fill: str,
                 anchor: str = "start", extra: str = "") -> str:
        self.used[face].update(s)
        family = self.faces[face].family
        esc = s.replace("&", "&amp;").replace("<", "&lt;")
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
                f'fill="{fill}" text-anchor="{anchor}" {extra}>{esc}</text>')

    def style(self) -> str:
        return "".join(self.faces[f].css("".join(sorted(chars))) for f, chars in self.used.items())


# --------------------------------------------------------------------------- data

def gh_json(*args: str):
    return json.loads(subprocess.run(["gh", "api", *args], check=True, capture_output=True, text=True).stdout)


def fetch() -> dict:
    rows = []
    for repo in REPOS:
        out = subprocess.run(
            ["gh", "api", "--paginate", f"repos/{repo}/commits?per_page=100",
             "--jq", '.[] | [.commit.author.date[0:7], .commit.author.name, (.author.type // "")] | @tsv'],
            check=True, capture_output=True, text=True,
        ).stdout
        for line in out.splitlines():
            month, author, kind = (line.split("\t") + ["", ""])[:3]
            if kind == "Bot" or author.endswith("[bot]") or author == "Vercel":
                continue
            rows.append([repo, month])
    prs = sum(
        gh_json(f"search/issues?q=repo:{repo}+is:pr+is:merged+merged:>={START}-01&per_page=1")["total_count"]
        for repo in REPOS
    )
    data = {"commits": rows, "merged_prs": prs, "fetched": date.today().isoformat()}
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "data.json").write_text(json.dumps(data))
    return data


def months_between(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# --------------------------------------------------------------------------- shapes

TOP_BODY = "M10 30 C10 36 24 46 32 57 C40 46 54 36 54 30 Z"
TOP_CAP = "M10 30 C10 25 19 19 32 19 C45 19 54 25 54 30 Z"


def top(theme: Theme, animated: bool) -> str:
    """The TopTop mark (64x64 units), spinning when `animated`."""
    grain = "".join(
        f'<path d="M8 {y} Q32 {y + 3.2} 56 {y}"/>' for y in (34, 38, 43, 48.5)
    )
    sheen = (
        '<rect class="sheen" x="-14" y="16" width="9" height="44" fill="#FFF8EC" opacity=".38"/>'
        if animated else ""
    )
    return f'''
<g class="{'wobble' if animated else ''}">
  <g transform="rotate(-12 32 34)">
    <rect x="29" y="6" width="6" height="14" rx="2.5" fill="{theme.handle}"/>
    <path d="{TOP_CAP}" fill="{theme.berry}"/>
    <path d="{TOP_BODY}" fill="{theme.birch}"/>
    <g clip-path="url(#body-{theme.name})">
      <g fill="none" stroke="{theme.grain}" stroke-width=".7">{grain}</g>
      {sheen}
    </g>
  </g>
</g>
<ellipse class="{'trace' if animated else ''}" cx="35" cy="59.2" rx="9" ry="1.6" fill="none" stroke="#9C7A4E" stroke-opacity=".55" stroke-width=".6"/>'''


def svg(width: int, height: int, title: str, theme: Theme, text: Text, body: str, css: str = "") -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="{title}">
<title>{title}</title>
<defs>
  <clipPath id="body-{theme.name}"><path d="{TOP_BODY}"/></clipPath>
  <clipPath id="card-{theme.name}"><rect width="{width}" height="{height}" rx="20"/></clipPath>
  <style>{text.style()}{css}</style>
</defs>
<rect width="{width}" height="{height}" rx="20" fill="{theme.paper}"/>
{body}
</svg>
'''


# --------------------------------------------------------------------------- banner

BANNER_CSS = """
.wobble{transform-box:view-box;transform-origin:32px 57px;animation:wobble 3.2s ease-in-out infinite}
.sheen{animation:spin .55s linear infinite}
.trace{transform-box:fill-box;transform-origin:center;animation:trace 3.2s ease-in-out infinite}
@keyframes wobble{0%,100%{transform:rotate(-4deg)}50%{transform:rotate(5deg)}}
@keyframes spin{from{transform:translateX(0)}to{transform:translateX(76px)}}
@keyframes trace{0%,100%{transform:translateX(-1.5px) scaleX(.9)}50%{transform:translateX(2px) scaleX(1.1)}}
@media (prefers-reduced-motion:reduce){.wobble,.sheen,.trace{animation:none}.sheen{display:none}}
"""


def banner(theme: Theme, faces: dict[str, Face]) -> str:
    t = Text(faces)
    w, h = 1280, 400
    rings = "".join(
        f'<ellipse cx="230" cy="214" rx="{r}" ry="{r * 0.94:.1f}" fill="none" stroke="{theme.rings}" stroke-width="{1.4 if i % 3 else 2.4}"/>'
        for i, r in enumerate(range(34, 250, 18))
    )
    mark = f'<g transform="translate(118 74) scale(3.6)">{top(theme, animated=True)}</g>'
    x = 470
    body = f'''
<g clip-path="url(#card-{theme.name})">{rings}</g>
{mark}
{t("display", "TopTop", x, 196, 128, theme.ink, extra='letter-spacing="-2"')}
{t("serif", "Steady, because it keeps moving.", x + 4, 252, 34, theme.body)}
{t("sans", "A software studio in Helsinki. We build our own products", x + 4, 306, 23, theme.muted)}
{t("sans", "and help other teams ship theirs.", x + 4, 338, 23, theme.muted)}
'''
    return svg(w, h, "TopTop: steady, because it keeps moving. A software studio in Helsinki.", theme, t, body, BANNER_CSS)


# --------------------------------------------------------------------------- activity chart

def activity(theme: Theme, faces: dict[str, Face], data: dict) -> str:
    t = Text(faces)
    today = date.fromisoformat(data["fetched"])
    end = f"{today.year:04d}-{today.month:02d}"
    months = months_between(START, end)
    counts: dict[str, Counter] = defaultdict(Counter)
    for repo, month in data["commits"]:
        if START <= month <= end:
            counts[month][REPOS[repo]] += 1
    total = sum(sum(c.values()) for c in counts.values())
    peak_month = max(months, key=lambda m: sum(counts[m].values()))
    peak = sum(counts[peak_month].values())

    w, h = 1280, 600
    left, right, top_y, base = 104, 1232, 200, 466
    step = 250
    ymax = ((peak // step) + 1) * step
    scale = (base - top_y) / ymax
    slot = (right - left) / len(months)
    bar = slot * 0.62

    parts = [
        t("serif", "What we shipped, month by month", 64, 82, 40, theme.ink),
        t("sans", f"{total:,} commits and {data['merged_prs']:,} merged pull requests across "
                  f"{len(REPOS)} product repositories since January 2025.", 64, 124, 24, theme.muted),
    ]
    # grid
    for v in range(0, ymax + 1, step):
        y = base - v * scale
        parts.append(f'<line x1="{left}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}" stroke="{theme.grid}" stroke-width="1"/>')
        parts.append(t("sans", f"{v:,}", left - 14, y + 6, 19, theme.muted, anchor="end"))
    # bars
    for i, m in enumerate(months):
        x = left + i * slot + (slot - bar) / 2
        y = base
        partial = m == end and today.day < 28
        for key, _ in SERIES:
            n = counts[m][key]
            if not n:
                continue
            hgt = n * scale
            y -= hgt
            op = ""
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar:.1f}" height="{hgt:.1f}" fill="{theme.series[key]}"{op}/>')
        mm = int(m[5:])
        if mm in (1, 4, 7, 10):
            label = ["Jan", "Apr", "Jul", "Oct"][(mm - 1) // 3]
            parts.append(t("sans", label, x + bar / 2, base + 30, 19, theme.muted, anchor="middle"))
            if mm == 1:
                parts.append(t("sans-bold", m[:4], x + bar / 2, base + 54, 19, theme.body, anchor="middle"))
        if m == peak_month:
            parts.append(t("sans-bold", f"{peak:,}", x + bar / 2, y - 12, 19, theme.ink, anchor="middle"))
        if partial:
            parts.append(t("sans", "so far", x + bar / 2, y - 12, 18, theme.muted, anchor="middle"))
    # legend
    lx, ly = 64, 164
    for key, label in SERIES:
        parts.append(f'<rect x="{lx}" y="{ly - 15}" width="16" height="16" rx="3" fill="{theme.series[key]}"/>')
        parts.append(t("sans", label, lx + 24, ly, 20, theme.body))
        lx += 24 + faces["sans"].width(label, 20) + 32
    # footnote
    parts.append(t("sans", f"Commits by people on default branches, bots excluded. Updated {today:%-d %B %Y}.",
                   64, h - 30, 18, theme.muted))

    title = f"{total:,} commits and {data['merged_prs']:,} merged pull requests since January 2025, by month and product."
    return svg(w, h, title, theme, t, "\n".join(parts))


# --------------------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="redraw from scripts/.cache/data.json")
    args = parser.parse_args()

    data = json.loads((CACHE / "data.json").read_text()) if args.offline else fetch()
    faces = load_faces()
    OUT.mkdir(parents=True, exist_ok=True)
    for theme in (LIGHT, DARK):
        (OUT / f"banner-{theme.name}.svg").write_text(banner(theme, faces))
        (OUT / f"activity-{theme.name}.svg").write_text(activity(theme, faces, data))
    for f in sorted(OUT.glob("*.svg")):
        print(f"{f.relative_to(ROOT)}  {f.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
