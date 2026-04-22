"""Strip HTML to plain text while preserving line breaks and list bullets."""
import re

from bs4 import BeautifulSoup, NavigableString

BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)


def strip_html(value: str | None) -> str | None:
    """Convert HTML fragment to readable plain text."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    # Normalize <br>, <br/>, <br /> → newline placeholder BEFORE parsing
    # (html.parser misinterprets <br>text</br> in bare HTML fragments).
    text = BR_RE.sub("\n", text)

    soup = BeautifulSoup(text, "html.parser")

    # Replace <br> with newline (kept in case any survived sub above)
    for br in list(soup.find_all("br")):
        br.replace_with(NavigableString("\n"))
    # Bullet list items
    for li in soup.find_all("li"):
        li.insert(0, NavigableString("- "))
        li.append(NavigableString("\n"))
    # Block-level newlines
    for tag in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "ul", "ol", "tr"]):
        tag.append(NavigableString("\n"))

    out = soup.get_text()
    # Collapse 3+ newlines, trim trailing spaces per line
    lines = [ln.rstrip() for ln in out.splitlines()]
    cleaned: list[str] = []
    blank_streak = 0
    for ln in lines:
        if ln.strip() == "":
            blank_streak += 1
            if blank_streak <= 1:
                cleaned.append("")
        else:
            blank_streak = 0
            cleaned.append(ln)
    result = "\n".join(cleaned).strip()
    return result or None
