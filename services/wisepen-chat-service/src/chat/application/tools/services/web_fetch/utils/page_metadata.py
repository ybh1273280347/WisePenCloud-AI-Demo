from chat.application.web_search.utils.domains import extract_domain


def extract_markdown_title(markdown: str) -> str:
    for line in markdown.splitlines()[:20]:
        text = line.strip()
        if text.startswith("# "):
            return text.removeprefix("# ").strip()

    return ""


def extract_page_domain(url: str) -> str:
    return extract_domain(url)
