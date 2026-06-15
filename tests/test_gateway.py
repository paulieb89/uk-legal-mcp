"""
Integration tests for the FastMCP v3 gateway.

Uses FastMCP's in-process Client to verify tool registration, schema
integrity, and offline tool execution without hitting any live APIs.
"""

import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway, _PROJECT_VERSION


EXPECTED_MODULES = [
    "case_law",
    "legislation",
    "parliament",
    "bills",
    "votes",
    "committees",
    "citations",
    "hmrc",
]


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as c:
        yield c


# ---------------------------------------------------------------------------
# Server identity
# ---------------------------------------------------------------------------


class TestServerIdentity:
    @pytest.mark.asyncio
    async def test_server_name(self, client: Client):
        result = client.initialize_result
        assert result is not None
        assert result.serverInfo.name == "uk-legal-mcp"

    @pytest.mark.asyncio
    async def test_server_version(self, client: Client):
        result = client.initialize_result
        assert result is not None
        assert result.serverInfo.version == _PROJECT_VERSION


# ---------------------------------------------------------------------------
# Tool listing and schema validation
# ---------------------------------------------------------------------------


class TestToolListing:
    @pytest.mark.asyncio
    async def test_lists_tools(self, client: Client):
        tools = await client.list_tools()
        assert len(tools) >= 24, f"Expected ≥24 tools, got {len(tools)}"

    @pytest.mark.asyncio
    async def test_all_modules_have_tools(self, client: Client):
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]
        for module in EXPECTED_MODULES:
            matching = [n for n in tool_names if n.startswith(f"{module}_")]
            assert matching, f"No tools found with namespace prefix '{module}_'"

    @pytest.mark.asyncio
    async def test_tool_schemas_have_descriptions(self, client: Client):
        tools = await client.list_tools()
        for tool in tools:
            assert tool.description, f"Tool '{tool.name}' has no description"

    @pytest.mark.asyncio
    async def test_tool_schemas_valid(self, client: Client):
        tools = await client.list_tools()
        for tool in tools:
            schema = tool.inputSchema
            assert schema.get("type") == "object", (
                f"Tool '{tool.name}' schema type is not 'object'"
            )

    @pytest.mark.asyncio
    async def test_companion_tools_registered(self, client: Client):
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        for name in ["judgment_get_header", "judgment_get_index", "judgment_get_paragraph"]:
            assert name in tool_names, f"Companion tool '{name}' not registered"


# ---------------------------------------------------------------------------
# Resource templates
# ---------------------------------------------------------------------------


class TestResources:
    @pytest.mark.asyncio
    async def test_resource_templates_listed(self, client: Client):
        templates = await client.list_resource_templates()
        uris = [t.uriTemplate for t in templates]
        assert any("judgment" in u for u in uris), (
            f"No judgment resource template found in {uris}"
        )


# ---------------------------------------------------------------------------
# Offline tool execution (citations — no network)
# ---------------------------------------------------------------------------


class TestFormatOscolaTools:
    @pytest.mark.asyncio
    async def test_format_neutral_uksc(self, client: Client):
        result = await client.call_tool(
            "citations_format_oscola",
            {"citation_type": "neutral", "confidence": 1.0, "resolved_url": "https://caselaw.nationalarchives.gov.uk/uksc/2024/12", "year": 2024, "court": "UKSC", "number": 12},
        )
        assert not result.is_error
        assert result.data["status"] == "ok"
        assert result.data["oscola"] == "[2024] UKSC 12"

    @pytest.mark.asyncio
    async def test_format_neutral_ewca_civ(self, client: Client):
        result = await client.call_tool(
            "citations_format_oscola",
            {"citation_type": "neutral", "confidence": 1.0, "resolved_url": "https://caselaw.nationalarchives.gov.uk/ewca/civ/2023/450", "year": 2023, "court": "EWCA CIV", "number": 450},
        )
        assert not result.is_error
        assert result.data["oscola"] == "[2023] EWCA Civ 450"

    @pytest.mark.asyncio
    async def test_format_law_report_with_volume(self, client: Client):
        result = await client.call_tool(
            "citations_format_oscola",
            {"citation_type": "law_report", "confidence": 0.9, "year": 2024, "report_series": "WLR", "volume": 1, "page": 100},
        )
        assert not result.is_error
        assert result.data["oscola"] == "[2024] 1 WLR 100"

    @pytest.mark.asyncio
    async def test_format_law_report_without_volume(self, client: Client):
        result = await client.call_tool(
            "citations_format_oscola",
            {"citation_type": "law_report", "confidence": 0.9, "year": 1932, "report_series": "AC", "page": 562},
        )
        assert not result.is_error
        assert result.data["oscola"] == "[1932] AC 562"

    @pytest.mark.asyncio
    async def test_format_legislation(self, client: Client):
        result = await client.call_tool(
            "citations_format_oscola",
            {"citation_type": "legislation", "confidence": 0.95, "section": "47", "legislation_title": "Companies Act 2006"},
        )
        assert not result.is_error
        assert result.data["oscola"] == "s.47 Companies Act 2006"

    @pytest.mark.asyncio
    async def test_format_si(self, client: Client):
        result = await client.call_tool(
            "citations_format_oscola",
            {"citation_type": "si", "confidence": 1.0, "si_year": 2018, "si_number": 1234},
        )
        assert not result.is_error
        assert result.data["oscola"] == "SI 2018/1234"

    @pytest.mark.asyncio
    async def test_refuses_confidence_zero(self, client: Client):
        result = await client.call_tool(
            "citations_format_oscola",
            {"citation_type": "neutral", "confidence": 0.0, "resolved_url": "https://caselaw.nationalarchives.gov.uk/ewca/civ/2022/9999", "year": 2022, "court": "EWCA CIV", "number": 9999},
        )
        assert not result.is_error
        assert result.data["status"] == "upstream_validation"
        assert "confidence 0.0" in result.data["detail"]

    @pytest.mark.asyncio
    async def test_refuses_neutral_without_resolved_url(self, client: Client):
        result = await client.call_tool(
            "citations_format_oscola",
            {"citation_type": "neutral", "confidence": 0.5, "resolved_url": None, "year": 2024, "court": "EWHC", "number": 100},
        )
        assert not result.is_error
        assert result.data["status"] == "upstream_validation"
        assert "resolved_url" in result.data["detail"]


class TestCitationTools:
    @pytest.mark.asyncio
    async def test_citations_parse(self, client: Client):
        result = await client.call_tool(
            "citations_parse",
            {"text": "The court applied Donoghue v Stevenson [1932] AC 562 and s.2 Occupiers' Liability Act 1957", "disambiguate": False},
        )
        assert not result.is_error, f"Tool error: {result.data}"
        assert result.data is not None

    @pytest.mark.asyncio
    async def test_citations_resolve(self, client: Client):
        result = await client.call_tool(
            "citations_resolve",
            {"citation": "[2024] UKSC 12"},
        )
        assert not result.is_error, f"Tool error: {result.data}"
        assert result.data is not None

    @pytest.mark.asyncio
    async def test_citations_resolve_fake_citation_not_verified(self, client: Client):
        """Fabricated neutral citation must return confidence 0.0, not verified."""
        result = await client.call_tool(
            "citations_resolve",
            {"citation": "[2022] EWCA Civ 9999"},
        )
        assert not result.is_error, f"Tool error: {result.data}"
        assert result.data is not None
        assert result.data.confidence == 0.0, (
            f"Expected confidence 0.0 for non-existent citation, got {result.data.confidence}"
        )
        # resolved_url is preserved so the caller can see what was attempted
        assert result.data.resolved_url is not None


# ---------------------------------------------------------------------------
# Custom HTTP routes (tested via ASGI test client)
# ---------------------------------------------------------------------------


class TestCustomRoutes:
    @pytest.mark.asyncio
    async def test_health_route(self):
        from starlette.testclient import TestClient

        app = gateway.http_app(
            path="/mcp",
            json_response=True,
            stateless_http=True,
        )
        with TestClient(app) as tc:
            resp = tc.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert body["modules"] == 8

    @pytest.mark.asyncio
    async def test_metrics_route(self):
        from starlette.testclient import TestClient

        app = gateway.http_app(
            path="/mcp",
            json_response=True,
            stateless_http=True,
        )
        with TestClient(app) as tc:
            resp = tc.get("/metrics")
            assert resp.status_code == 200
            assert "uk_legal_tool_calls_total" in resp.text

    @pytest.mark.asyncio
    async def test_server_card_route(self):
        from starlette.testclient import TestClient

        app = gateway.http_app(
            path="/mcp",
            json_response=True,
            stateless_http=True,
        )
        with TestClient(app) as tc:
            resp = tc.get("/.well-known/mcp/server-card.json")
            assert resp.status_code == 200
            body = resp.json()
            assert body["serverInfo"]["name"] == "uk-legal-mcp"
            assert body["serverInfo"]["version"] == _PROJECT_VERSION

    @pytest.mark.asyncio
    async def test_glama_route(self):
        from starlette.testclient import TestClient

        app = gateway.http_app(
            path="/mcp",
            json_response=True,
            stateless_http=True,
        )
        with TestClient(app) as tc:
            resp = tc.get("/.well-known/glama.json")
            assert resp.status_code == 200
            body = resp.json()
            assert "maintainers" in body
