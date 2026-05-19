from __future__ import annotations

from typing import List, Optional
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

from ..identifiers import extract_arxiv_id_from_url
from ..models import ArxivDeltaRecord

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def parse_arxiv_atom_feed(xml_text: str, *, source_feed: str) -> List[ArxivDeltaRecord]:
    root = ElementTree.fromstring(xml_text)
    records: List[ArxivDeltaRecord] = []

    for entry in root.findall(f"{ATOM_NS}entry"):
        record = _parse_entry(entry, source_feed=source_feed)
        if record is not None:
            records.append(record)

    return records


def _parse_entry(entry: Element, *, source_feed: str) -> Optional[ArxivDeltaRecord]:
    abs_url = _entry_id(entry) or _link_href(entry, rel="alternate")
    if not abs_url:
        return None

    arxiv_id = extract_arxiv_id_from_url(abs_url)
    if not arxiv_id:
        return None

    title = _text(entry.find(f"{ATOM_NS}title"))
    if not title:
        return None

    return ArxivDeltaRecord(
        arxiv_id=arxiv_id,
        title=title,
        abstract=_text(entry.find(f"{ATOM_NS}summary")),
        authors=[
            name
            for author in entry.findall(f"{ATOM_NS}author")
            for name in [_text(author.find(f"{ATOM_NS}name"))]
            if name
        ],
        published_date=_text(entry.find(f"{ATOM_NS}published")),
        updated_date=_text(entry.find(f"{ATOM_NS}updated")),
        categories=[
            term
            for category in entry.findall(f"{ATOM_NS}category")
            for term in [category.attrib.get("term")]
            if term
        ],
        abs_url=abs_url,
        pdf_url=_pdf_url_from_abs_url(abs_url),
        source_feed=source_feed,
    )


def _entry_id(entry: Element) -> Optional[str]:
    return _text(entry.find(f"{ATOM_NS}id"))


def _link_href(entry: Element, *, rel: str) -> Optional[str]:
    for link in entry.findall(f"{ATOM_NS}link"):
        if link.attrib.get("rel") == rel and link.attrib.get("href"):
            return link.attrib["href"]
    return None


def _text(element: Element | None) -> Optional[str]:
    if element is None or not element.text:
        return None
    text = " ".join(element.text.split()).strip()
    return text or None


def _pdf_url_from_abs_url(abs_url: str) -> str:
    if "/abs/" in abs_url:
        return abs_url.replace("/abs/", "/pdf/", 1)
    return abs_url.rstrip("/") + ".pdf"
