"""The documentation (docs/documentation.md) as linked web pages, one per chapter.

Chapters are the ``## `` headings of the file, grouped by the ``# Part …``
headings above them. Links between chapters (``#5-how-papers-are-recommended``,
``#rules-you-can-set-defaults``) are rewritten to point to the right chapter
page; links to files outside the docs (``../LICENSE``) become plain text.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import markdown
from markdown.extensions.toc import slugify

# docs/documentation.md; web/documentation.md is a symlink to it that the
# package build turns into a copy, so the docs also ship in wheels
DEFAULT_PATH = Path(__file__).parent / "documentation.md"
EXTENSIONS = ["tables", "fenced_code", "toc", "sane_lists"]


@dataclass
class Section:
    slug: str
    title: str
    level: int


@dataclass
class Chapter:
    slug: str
    title: str
    part: str
    source: str
    html: str = ""
    sections: list[Section] = field(default_factory=list)
    ids: set[str] = field(default_factory=set)


@dataclass
class Documentation:
    title: str
    intro_html: str
    chapters: list[Chapter]

    def chapter(self, slug: str) -> Chapter | None:
        return next((c for c in self.chapters if c.slug == slug), None)

    def parts(self) -> list[tuple[str, list[Chapter]]]:
        grouped: list[tuple[str, list[Chapter]]] = []
        for chapter in self.chapters:
            if not grouped or grouped[-1][0] != chapter.part:
                grouped.append((chapter.part, []))
            grouped[-1][1].append(chapter)
        return grouped

    def neighbours(self, slug: str) -> tuple[Chapter | None, Chapter | None]:
        index = [c.slug for c in self.chapters].index(slug)
        before = self.chapters[index - 1] if index > 0 else None
        after = self.chapters[index + 1] if index + 1 < len(self.chapters) else None
        return before, after


def _render(text: str) -> tuple[str, list]:
    md = markdown.Markdown(extensions=EXTENSIONS)
    html = md.convert(text)
    return html, md.toc_tokens


def _flatten(tokens: list, out: list[Section]) -> None:
    for token in tokens:
        out.append(Section(token["id"], re.sub(r"<[^>]+>", "", token["name"]), token["level"]))
        _flatten(token["children"], out)


def _split(text: str) -> tuple[str, str, list[tuple[str, str, str]]]:
    """Title, intro and (part, heading, body) per chapter; code blocks are respected."""
    title, intro, chapters = None, [], []
    part, current, fence = "", None, False  # current: [part, heading, lines]
    for line in text.splitlines():
        if line.startswith("```"):
            fence = not fence
        if not fence and line.startswith("# "):
            if title is None:
                title = line[2:].strip()
            else:
                part = line[2:].strip()
            continue
        if not fence and line.startswith("## "):
            current = [part, line[3:].strip(), []]
            chapters.append(current)
            continue
        if not fence and line.strip() == "---":
            continue  # part separators
        (current[2] if current else intro).append(line)
    return (
        title or "Documentation",
        "\n".join(intro),
        [(p, h, "\n".join(lines)) for p, h, lines in chapters],
    )


def _rewrite_links(html: str, here: str, owner: dict[str, str]) -> str:
    def anchor(match: re.Match) -> str:
        target = match.group(1)
        chapter = owner.get(target)
        if chapter is None:
            return match.group(0)
        if chapter == target:
            return f'href="/docs/{chapter}"'
        if chapter == here:
            return match.group(0)
        return f'href="/docs/{chapter}#{target}"'

    html = re.sub(r'href="#([^"]+)"', anchor, html)
    # files of the repository have no page: keep the text, drop the link
    html = re.sub(r'<a href="\.\./[^"]*">(.*?)</a>', r"\1", html)
    # external links open in a new tab
    return re.sub(
        r'<a href="(https?://[^"]+)"', r'<a href="\1" target="_blank" rel="noopener"', html
    )


def load(path: Path = DEFAULT_PATH) -> Documentation:
    """Parse the documentation file into chapters with rendered HTML and sections."""
    title, intro, parts = _split(path.read_text(encoding="utf-8"))
    # the file's own table of contents is replaced by the navigation
    intro = re.split(r"^## Contents\s*$", intro, flags=re.M)[0]
    chapters = []
    for part, heading, body in parts:
        if heading.lower() == "contents":
            continue
        slug = slugify(heading, "-")
        html, tokens = _render(body)
        chapter = Chapter(slug=slug, title=heading, part=part, source=body, html=html)
        _flatten(tokens, chapter.sections)
        chapter.ids = {s.slug for s in chapter.sections}
        chapters.append(chapter)
    owner = {c.slug: c.slug for c in chapters}
    for chapter in chapters:
        for slug in chapter.ids:
            owner.setdefault(slug, chapter.slug)
    for chapter in chapters:
        chapter.html = _rewrite_links(chapter.html, chapter.slug, owner)
    intro_html = _rewrite_links(_render(intro)[0], "", owner)
    return Documentation(title=title, intro_html=intro_html, chapters=chapters)


class DocsCache:
    """Reload the documentation only when the file changes."""

    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self._stamp: float | None = None
        self._docs: Documentation | None = None

    def get(self) -> Documentation | None:
        if not self.path.exists():
            return None
        stamp = self.path.stat().st_mtime
        if self._docs is None or stamp != self._stamp:
            self._docs, self._stamp = load(self.path), stamp
        return self._docs
