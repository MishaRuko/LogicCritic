"""Fetch reproducible open-access evaluation packets from Europe PMC."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

EUROPE_PMC_XML = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
MAX_BODY_CHARS = 80_000


def _text(element: ET.Element | None) -> str:
    return " ".join(element.itertext()).strip() if element is not None else ""


def packet_from_xml(pmcid: str, xml: str) -> dict:
    root = ET.fromstring(xml)
    title = _text(root.find(".//article-title")) or pmcid
    abstract = _text(root.find(".//abstract"))
    body_element = root.find(".//body")
    if body_element is None or not _text(body_element):
        raise ValueError(f"{pmcid} has no article body")
    # References, acknowledgements and supplements add substantial context cost but do not form the
    # evidence body being evaluated. The cap keeps every paired run within the declared pilot budget.
    parts = [f"# {title}", "## Abstract", abstract, "## Article body"]
    for element in body_element.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        content = _text(element)
        if not content:
            continue
        if tag == "title":
            parts.append(f"### {content}")
        elif tag in {"p", "tr"}:
            parts.append(content)
    text = "\n\n".join(parts)
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS].rsplit(" ", 1)[0] + "\n\n[Article body truncated at evaluation packet limit.]"
    return {
        "id": pmcid,
        "sources": [
            {
                "title": title,
                "text": text,
                "source_url": EUROPE_PMC_XML.format(pmcid=pmcid),
                "source_kind": "Europe PMC open-access article body",
            }
        ],
    }


async def fetch_packet(pmcid: str) -> dict:
    pmcid = pmcid.upper()
    if not pmcid.startswith("PMC"):
        raise ValueError("A PMCID must start with PMC")
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(EUROPE_PMC_XML.format(pmcid=pmcid))
        response.raise_for_status()
    return packet_from_xml(pmcid, response.text)


async def fetch_packets(pmcids: list[str], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for pmcid in pmcids:
        packet = await fetch_packet(pmcid)
        path = output_dir / f"{packet['id'].lower()}.json"
        path.write_text(json.dumps(packet, indent=2) + "\n")
        paths.append(path)
    return paths
