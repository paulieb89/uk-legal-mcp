"""Pydantic models for the parliament module."""

from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HansardContribution(BaseModel):
    """A single Hansard debate contribution with citation-grade metadata.

    Field provenance maps to the upstream hansard-api.parliament.uk
    `Contributions[i]` payload, so callers can footnote a contribution
    without re-querying.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    member_name: str = Field(..., description="Name of the contributing member (MemberName)")
    member_id: int | None = Field(None, description="Members API integer ID. Use to call parliament_member_debates or parliament_member_interests.")
    attributed_to: str = Field(..., description="Full citable attribution string, e.g. 'The Minister of State, DESNZ (Lord Whitehead) (Lab)'. Includes role-at-time of contribution for ministerial interventions.")
    party: str | None = Field(None, description="Political party affiliation parsed from the trailing '(Party)' suffix in AttributedTo")
    constituency: str | None = Field(None, description="Constituency (Commons only; None for Lords)")
    date: Date = Field(..., description="Date the contribution was made (SittingDate)")
    debate_title: str = Field(..., description="Title of the debate or question (DebateSection)")
    debate_id: int = Field(..., description="Integer DebateSectionId — internal Hansard identifier")
    debate_ext_id: str = Field(..., description="DebateSectionExtId GUID. Use as {debate_ext_id} in hansard://debate/{debate_ext_id}/header.")
    contribution_ext_id: str = Field(..., description="ContributionExtId GUID — stable citation key. Use as {contribution_ext_id} in hansard://debate/{debate_ext_id}/contribution/{contribution_ext_id}.")
    column_ref: str | None = Field(None, description="Hansard column-section identifier from HansardSection (e.g. 'BE-BG1', 'AA-AD'). Note: identifies the column-range block, not the OSCOLA column number directly.")
    chamber_section: str = Field(..., description="Hansard section bucket (Section): 'Commons Chamber', 'Lords Chamber', 'Westminster Hall', 'Written Answers', 'Written Statements'.")
    house: Literal["Commons", "Lords"] = Field(..., description="House of Parliament (House)")
    rank: int | None = Field(None, description="Upstream relevance score (Rank). Higher = more relevant to the query.")
    text: str = Field(..., description="Contribution text (capped per max_text_chars on the input; default 3000). Source: ContributionTextFull when text_mode='full', ContributionText preview otherwise.")
    url: str = Field(..., description="Public hansard.parliament.uk URL for this contribution (synthesised from House/Date/DebateSectionExtId/ContributionExtId).")


class HansardSearchResult(BaseModel):
    """Result of a Hansard debate search.

    Wraps the list of matching contributions with the query, filters,
    and corpus-level facet counts so the client sees a real nested object
    on the wire.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(..., description="The phrase that was searched in Hansard")
    from_date: Date | None = Field(None, description="Start date filter applied, if any")
    to_date: Date | None = Field(None, description="End date filter applied, if any")
    house: Literal["Commons", "Lords", "both"] = Field("both", description="House filter applied")
    member: str | None = Field(None, description="Member name filter applied, if any")
    text_mode: Literal["preview", "full"] = Field("preview", description="Whether contribution `text` carries the upstream preview or full body (still capped).")
    offset: int = Field(0, description="Skip applied to this page (Hansard API: skip)")
    limit: int = Field(20, description="Page size requested")
    total: int = Field(..., description="Number of contributions returned in this call")
    total_corpus: int | None = Field(None, description="Total contributions in Hansard matching this query (TotalContributions). Use to decide whether to paginate further or escalate to parliament_policy_position_summary.")
    party_breakdown: dict[str, int] = Field(default_factory=dict, description="Counts by party across the returned page")
    house_breakdown: dict[str, int] = Field(default_factory=dict, description="Counts by house across the returned page")
    date_range: tuple[Date, Date] | None = Field(None, description="(min, max) SittingDate of returned contributions, or None if empty")
    has_more: bool = Field(False, description="True if a full page was returned (more may exist; re-call with offset=offset+limit)")
    contributions: list[HansardContribution] = Field(
        default_factory=list,
        description="Matching Hansard contributions with full citation metadata.",
    )


class MemberDebatesResult(BaseModel):
    """Result of a member-filtered Hansard search.

    Wraps the contributions for one member (optionally filtered further
    by topic) with query metadata.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    member_id: int = Field(..., description="Parliament Members API member ID")
    topic: str | None = Field(None, description="Topic phrase filter applied, if any")
    offset: int = Field(0, description="Skip applied to this page")
    limit: int = Field(20, description="Page size requested")
    total: int = Field(..., description="Number of contributions returned in this call")
    has_more: bool = Field(False, description="True if a full page was returned (more may exist)")
    contributions: list[HansardContribution] = Field(
        default_factory=list,
        description="Hansard contributions for the member. Each `text` field is capped at 3000 characters.",
    )


class FacetCount(BaseModel):
    """A single facet bucket: a key (party / year / member / etc.) and its count."""

    model_config = ConfigDict(str_strip_whitespace=True)

    key: str = Field(..., description="Bucket key (e.g. 'Lab', '2024', 'Lord Whitehead')")
    count: int = Field(..., ge=0, description="Number of contributions in this bucket")


class TopContributor(BaseModel):
    """A top-volume contributor in the sampled window."""

    model_config = ConfigDict(str_strip_whitespace=True)

    member_id: int = Field(..., description="Members API ID. Use with parliament_member_debates.")
    member_name: str = Field(..., description="Member display name")
    party: str | None = Field(None, description="Party affiliation parsed from AttributedTo")
    count: int = Field(..., ge=0, description="Number of contributions on this topic in the sampled window")


class TopDebate(BaseModel):
    """A top debate section by contribution volume in the sampled window."""

    model_config = ConfigDict(str_strip_whitespace=True)

    debate_id: int = Field(..., description="Internal Hansard debate ID (DebateSectionId)")
    debate_ext_id: str = Field(..., description="Debate GUID. Use as {debate_ext_id} in hansard://debate/{debate_ext_id}/header.")
    debate_title: str = Field(..., description="Debate title")
    date: Date = Field(..., description="Sitting date of the debate")
    house: Literal["Commons", "Lords"] = Field(..., description="House")
    contribution_count: int = Field(..., ge=0, description="Number of contributions in this debate matching the topic")


class PolicyPositionSummary(BaseModel):
    """Aggregate, member-agnostic Hansard signals on a topic.

    Pure counts — no editorial labels, no LLM. Returned by
    parliament_policy_position_summary. Callers interpret the signals
    themselves and drill into specific contributions via the
    parliament_search_hansard tool or the hansard:// resources.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    topic: str = Field(..., description="Phrase searched in Hansard")
    from_date: Date | None = Field(None, description="Start date filter applied")
    to_date: Date | None = Field(None, description="End date filter applied")
    house: Literal["Commons", "Lords", "both"] = Field(..., description="House filter applied")
    total_contributions: int = Field(..., ge=0, description="Total contributions in Hansard matching topic+filters (TotalContributions)")
    total_debates: int = Field(..., ge=0, description="Total distinct debates touching this topic (TotalDebates)")
    total_written_statements: int = Field(..., ge=0, description="TotalWrittenStatements upstream count")
    total_written_answers: int = Field(..., ge=0, description="TotalWrittenAnswers upstream count")
    total_divisions: int = Field(..., ge=0, description="TotalDivisions upstream count. Non-zero → consider votes_search_divisions.")
    debates_scanned: int = Field(..., ge=0, description="Number of debates pulled from /search/Debates.json for the facet breakdown (≤ max_debates_scanned)")
    by_party: list[FacetCount] = Field(default_factory=list, description=(
        "Counts by party. ALWAYS EMPTY in this summary — Hansard's search "
        "API only exposes member identifiers at the per-debate level, not "
        "the corpus level. For party breakdown within one debate, read "
        "hansard://debate/{ext_id}/header. For one member's contributions "
        "across the corpus, use parliament_member_debates."
    ))
    by_house: list[FacetCount] = Field(default_factory=list, description="Counts of debates by house (Commons vs Lords)")
    by_section: list[FacetCount] = Field(default_factory=list, description="Counts of debates by Hansard section bucket (Chamber / Westminster Hall / Written Answers / Written Statements)")
    by_year: list[FacetCount] = Field(default_factory=list, description="Counts of debates by sitting year, desc by year")
    by_month_recent_12: list[FacetCount] = Field(default_factory=list, description="Counts of debates by YYYY-MM for the most recent 12 months in the sample, desc by month")
    top_contributors: list[TopContributor] = Field(default_factory=list, description=(
        "ALWAYS EMPTY in this summary — see by_party note. Use "
        "parliament_member_debates after picking a debate from top_debates."
    ))
    top_debates: list[TopDebate] = Field(default_factory=list, description=(
        "Top 20 debates ranked by upstream search relevance (Rank), with "
        "debate_ext_id for hansard://debate/{debate_ext_id}/header lookup. "
        "contribution_count in this list carries the upstream Rank score, "
        "not an actual contribution count (which requires fetching each "
        "debate's full Items list — too costly for a summary)."
    ))


class MemberResult(BaseModel):
    """A current or former Member of Parliament or Lord."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Parliament Members API member ID")
    name: str = Field(..., description="Full display name")
    party: str = Field(..., description="Current or last party affiliation")
    constituency: str | None = Field(None, description="Constituency (Commons); None for Lords")
    house: Literal["Commons", "Lords"] = Field(..., description="House of Parliament")
    is_current: bool = Field(..., description="Whether the member currently sits")


class MemberSearchResult(BaseModel):
    """Result of a parliament member name search.

    Wraps the list of matching members with search metadata so the
    LLM client sees a real nested object on the wire rather than a
    stringified JSON blob.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(..., description="The name that was searched")
    total: int = Field(..., description="Number of members matching the query")
    members: list[MemberResult] = Field(
        default_factory=list,
        description=(
            "Matching members. Use the integer `id` field from any member "
            "to call parliament_member_debates or parliament_member_interests."
        ),
    )


class Interest(BaseModel):
    """A single registered financial interest."""

    model_config = ConfigDict(str_strip_whitespace=True)

    category: str = Field(..., description="Interest category (e.g. 'Directorships', 'Donations')")
    description: str = Field(..., description="Description of the interest")
    date_created: Date | None = Field(None, description="Date the interest was registered")
    date_amended: Date | None = Field(None, description="Date the interest was last amended")


class MemberInterestsPage(BaseModel):
    """A page of registered financial interests for a member.

    Returned by parliament_member_interests. Callers paginate by
    re-calling with offset=offset+returned while has_more is True.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    member_id: int = Field(..., description="Parliament Members API member ID")
    category: str | None = Field(
        None,
        description="Category filter applied to this query, or None for all categories",
    )
    offset: int = Field(..., description="Number of interests skipped before this page")
    limit: int = Field(..., description="Max interests requested for this page")
    returned: int = Field(..., description="Number of interests actually returned in this call")
    has_more: bool = Field(
        ...,
        description=(
            "True if there may be more interests beyond this page. "
            "Re-call with offset=offset+returned to fetch the next page."
        ),
    )
    interests: list[Interest] = Field(
        default_factory=list,
        description=(
            "The interests in this page. `description` text is capped per "
            "the max_description_chars input parameter."
        ),
    )


class PetitionSummary(BaseModel):
    """A UK Parliament petition."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Petition ID")
    action: str = Field(..., description="Petition title / call to action")
    state: str = Field(..., description="Petition state (open, closed, etc.)")
    signature_count: int = Field(..., description="Number of signatures")
    created_at: Date | None = Field(None, description="Date the petition was created")
    government_response_at: Date | None = Field(None, description="Date of government response, if any")
    debate_date: Date | None = Field(None, description="Date the petition was debated, if any")
    url: str = Field(..., description="Petition URL on petition.parliament.uk")


class PetitionSearchResult(BaseModel):
    """Result of a UK Parliament petitions search.

    Wraps the matching petitions with the originating query and state
    filter.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(..., description="The term that was searched in petitions")
    state: Literal["open", "closed", "all"] = Field(
        ..., description="Petition state filter applied to this query"
    )
    offset: int = Field(0, description="Skip applied to this page")
    limit: int = Field(20, description="Page size requested")
    total: int = Field(..., description="Number of petitions returned in this call")
    has_more: bool = Field(False, description="True if a full page was returned (more may exist)")
    petitions: list[PetitionSummary] = Field(
        default_factory=list,
        description="Matching petitions (title, state, signature count, key dates, URL).",
    )
