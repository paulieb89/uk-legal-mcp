"""Single edge adapter for parsing external XML.

External XML — anything received over the network from upstreams like
legislation.gov.uk or caselaw.nationalarchives.gov.uk — MUST go through
parse_xml(). Direct calls to lxml.etree.fromstring() on untrusted content
are forbidden.

Defense in depth — two layers:

  1. Pre-parse rejection of DTD constructs. Legitimate CLML, Atom, and
     LegalDocML responses contain no DOCTYPE or ENTITY declarations. We
     reject any payload that does, returning XMLSyntaxError. This is the
     strict, explicit defense — easy to test, easy to reason about.

  2. Hardened lxml.etree.XMLParser. Even if the pre-parse check were
     bypassed (e.g. unicode escapes hiding the construct), the parser
     refuses to expand entities or fetch external resources.

What this prevents:

  - XXE (XML External Entity): a malicious or compromised upstream can
    include <!ENTITY xxe SYSTEM "file:///etc/passwd"> in its response;
    raw lxml expands the entity and leaks file contents into our parse
    result. Both layers refuse.
  - Billion laughs: nested entity expansion (1KB of XML → gigabytes of
    string) burns RAM until OOM. Layer 1 refuses; layer 2 wouldn't expand.
  - External DTDs: <!DOCTYPE ... SYSTEM "http://attacker/timing.dtd">
    forces an outbound request the user didn't authorize. Both layers
    refuse.

legislation.gov.uk and TNA Find Case Law are reputable government
endpoints — but "trusted upstream" doesn't mean immune to TLS MITM,
upstream compromise, or cache poisoning. The cost of routing through
this adapter is zero on legitimate content; the cost of NOT routing is
the entire XXE / billion-laughs attack surface.

Implementation note: defusedxml.lxml is deprecated upstream. The
recommended approach is to configure lxml.etree.XMLParser with secure
flags directly + explicit DTD rejection, which is what we do here.

Use lxml.html.fromstring for HTML scraping — that's a different parser
with different attack surfaces, not in scope here.
"""

from __future__ import annotations

import re

from lxml import etree


# DOCTYPE / ENTITY declarations are XML constructs that have no place in
# the CLML / Atom / LegalDocML payloads we receive. Reject them at the
# byte level before parsing, so the parser never even sees them.
_DTD_PATTERN = re.compile(rb"<!\s*(DOCTYPE|ENTITY|NOTATION|ELEMENT|ATTLIST)\b", re.IGNORECASE)


def _make_parser() -> etree.XMLParser:
    """Build a hardened XML parser (defense layer 2).

    - resolve_entities=False — refuse entity expansion (XXE, billion laughs)
    - no_network=True — refuse to fetch external resources (DTDs, schemas)
    - load_dtd=False — refuse to load DTD declarations at all
    - huge_tree=False — default cap on tree size (DoS guard)
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    )


def parse_xml(xml_content: bytes | str) -> etree._Element:
    """Parse external XML with XXE / entity-bomb / external-DTD protection.

    Accepts bytes (preferred, no encoding ambiguity) or str.

    Raises lxml.etree.XMLSyntaxError on:
      - DTD or ENTITY declarations (rejected pre-parse — defense layer 1)
      - malformed XML (raised by the hardened parser — defense layer 2)
    """
    if isinstance(xml_content, str):
        xml_content = xml_content.encode("utf-8")

    if _DTD_PATTERN.search(xml_content):
        raise etree.XMLSyntaxError(
            "XML payload contains DTD/ENTITY declaration — rejected for "
            "safety. Legitimate CLML/Atom/LegalDocML payloads do not use "
            "DTDs; if this is a real upstream response, it has been "
            "compromised or the parser needs a known-safe carve-out.",
            0, 0, 0,
        )

    return etree.fromstring(xml_content, parser=_make_parser())
