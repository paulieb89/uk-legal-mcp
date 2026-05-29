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
    member_id: int | None = Field(None, description=(
        "Members API integer ID. Use as {member_id} in "
        "hansard://member/{member_id}/biography for the member's role history."
    ))
    attributed_to: str = Field(..., description="Full citable attribution string, e.g. 'The Minister of State, DESNZ (Lord Whitehead) (Lab)'. Includes role-at-time of contribution for ministerial interventions.")
    party: str | None = Field(None, description="Political party affiliation parsed from the trailing '(Party)' suffix in AttributedTo")
    constituency: str | None = Field(None, description="Constituency (Commons only; None for Lords)")
    date: Date = Field(..., description="Date the contribution was made (SittingDate)")
    debate_title: str = Field(..., description="Title of the debate or question (DebateSection)")
    debate_id: int = Field(..., description="Integer DebateSectionId — internal Hansard identifier")
    debate_ext_id: str = Field(..., description="DebateSectionExtId GUID. Use as {debate_ext_id} in hansard://debate/{debate_ext_id}/header.")
    contribution_ext_id: str = Field(..., description="ContributionExtId GUID — stable citation key. Use as {contribution_ext_id} in hansard://debate/{debate_ext_id}/contribution/{contribution_ext_id}.")
    column_ref: str | None = Field(None, description=(
        "Hansard internal page-marker code from HansardSection (e.g. 'BE-BG1', "
        "'AA-AD'). NOT the citable column number that appears in an OSCOLA "
        "footnote — see `column_start` / `column_end` for those, which are "
        "only available when reading the contribution via "
        "hansard://debate/{debate_ext_id}/contribution/{contribution_ext_id}. "
        "From parliament_search_hansard, only this page-marker is exposed."
    ))
    column_start: int | None = Field(None, description=(
        "The citable Hansard column number where this contribution begins, "
        "e.g. 200, 1162. Extracted from `data-column-number` markers in the "
        "contribution HTML. Populated only when reading via the contribution "
        "resource — `parliament_search_hansard` returns this as None because "
        "Hansard's /search.json strips the column markers. To get the real "
        "column number for a search hit, read "
        "hansard://debate/{debate_ext_id}/contribution/{contribution_ext_id}."
    ))
    column_end: int | None = Field(None, description=(
        "The last Hansard column number this contribution spans, e.g. when a "
        "long speech runs across columns 200–203. Equal to `column_start` for "
        "short contributions. Same population semantics as `column_start`."
    ))
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
    member_id: int | None = Field(None, description="Members API integer ID filter applied, if any (echoed from input).")
    text_mode: Literal["preview", "full"] = Field("preview", description="Whether contribution `text` carries the upstream preview or full body (still capped).")
    offset: int = Field(0, description="Skip applied to this page (Hansard API: skip)")
    limit: int = Field(20, description="Page size requested")
    total: int = Field(..., description="Number of contributions returned in this call")
    total_corpus: int | None = Field(None, description="Total contributions in Hansard matching this query (TotalContributions). Use to decide whether to paginate further or escalate to parliament_policy_position_summary.")
    # Corpus envelope from /search.json — informative integers, no extra HTTP.
    total_debates: int | None = Field(None, description="TotalDebates — distinct debates touching this topic.")
    total_divisions: int | None = Field(None, description="TotalDivisions. Non-zero → consider `top_divisions` previews below or chain to votes_search_divisions.")
    total_written_statements: int | None = Field(None, description="TotalWrittenStatements.")
    total_written_answers: int | None = Field(None, description="TotalWrittenAnswers.")
    total_corrections: int | None = Field(None, description="TotalCorrections — published corrections to the Hansard record.")
    total_petitions: int | None = Field(None, description="TotalPetitions.")
    total_committees: int | None = Field(None, description="TotalCommittees.")
    total_members: int | None = Field(None, description="TotalMembers — member-name matches in the corpus.")
    # Two high-value preview arrays only — others omitted per recurring-cost reasoning.
    # Forward-ref strings because TopDebate / DivisionMatchLite are defined later in this file;
    # resolved by model_rebuild() at the bottom of the module.
    top_debates: "list[TopDebate]" = Field(default_factory=list, description=(
        "Top-ranked debates touching this topic (from upstream Debates[] preview, capped at 4 by Hansard's /search.json). "
        "Each entry's `debate_ext_id` chains to hansard://debate/{debate_ext_id}/header."
    ))
    top_divisions: "list[DivisionMatchLite]" = Field(default_factory=list, description=(
        "Top-ranked divisions touching this topic (from upstream Divisions[] preview, capped at 4). "
        "Each entry's `id` chains to votes_get_division; `debate_section_ext_id` chains back to the parent debate."
    ))
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

    member_id: int = Field(..., description=(
        "Members API integer ID. Use as {member_id} in "
        "hansard://member/{member_id}/biography for the member's role history."
    ))
    member_name: str = Field(..., description="Member display name")
    party: str | None = Field(None, description="Party affiliation parsed from AttributedTo")
    count: int = Field(..., ge=0, description="Number of contributions on this topic in the sampled window")


class TopDebate(BaseModel):
    """A top debate section returned as a search/lookup preview or aggregate result."""

    model_config = ConfigDict(str_strip_whitespace=True)

    debate_id: int = Field(..., description="Internal Hansard debate ID (DebateSectionId)")
    debate_ext_id: str = Field(..., description="Debate GUID. Use as {debate_ext_id} in hansard://debate/{debate_ext_id}/header.")
    debate_title: str = Field(..., description="Debate title")
    date: Date = Field(..., description="Sitting date of the debate")
    house: Literal["Commons", "Lords"] = Field(..., description="House")
    relevance_rank: int | None = Field(
        None, ge=0,
        description=(
            "Upstream Hansard relevance score for this debate against the search query (0–100). "
            "Populated for relevance-scored searches (parliament_search_hansard, "
            "parliament_policy_position_summary). Null for column-lookup matches — "
            "the column-search endpoint does not rank."
        ),
    )
    contribution_count: int | None = Field(
        None, ge=0,
        description=(
            "Number of contributions in this debate. Populated only when the emitting tool "
            "fetches the debate's full Items list (today: parliament_lookup_by_column). "
            "Null on preview arrays where the secondary fetch is too costly."
        ),
    )
    source_code: int | None = Field(
        None,
        description=(
            "Raw Hansard Overview.Source ordinal: 1=RollingHansard, 2=DailyHansard, "
            "3=BoundVolume, 4=Historic. Populated only when the emitting tool fetches the "
            "debate payload (today: parliament_lookup_by_column); null otherwise."
        ),
    )
    source: str | None = Field(
        None,
        description=(
            "Human-readable Hansard publication state (Swagger Source enum name). Tells a "
            "lawyer the citation's finality — RollingHansard/DailyHansard are pre-consolidation, "
            "BoundVolume/Historic are final bound volumes. Does NOT indicate whether the citation "
            "resolves: column lookup resolves across all four states."
        ),
    )


class DivisionMatchLite(BaseModel):
    """A single division (formal parliamentary vote) — lightweight match shape.

    Returned as a preview slice on `parliament_search_hansard`'s `top_divisions`
    and as the full element of `parliament_get_debate_divisions`'s output.

    Field provenance maps to the upstream `DivisionOverview` Swagger definition;
    the live `/debates/divisions/{ext}` endpoint returns most integer/boolean
    values as strings, which the parser converts.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description=(
        "Hansard-side division ID — distinct ID-space from the Lords/Commons Votes API. "
        "This value does NOT chain to votes_get_division (different API). For per-member "
        "voting, see `votes_id` below — populated when cross-resolution succeeded."
    ))
    votes_id: int | None = Field(None, description=(
        "Lords/Commons Votes API division ID, cross-resolved from the upstream by "
        "(date + house + number). Use as the `division_id` input to votes_get_division "
        "for the full member-by-member voting record. None when the cross-resolve found "
        "no match (e.g. some Daily Part divisions are recorded in Hansard before the "
        "Votes API publishes them)."
    ))
    external_id: str = Field(..., description="Division GUID (ExternalId). Stable identifier for cross-reference.")
    number: str = Field(..., description="Division number within the sitting (e.g. '1', '2').")
    date: Date = Field(..., description="Sitting date of the division (Date).")
    time: str | None = Field(None, description="Time of the division as HH:MM:SS, or None when the upstream omits it.")
    house: Literal["Commons", "Lords"] = Field(..., description="House where the division was held.")
    ayes_count: int = Field(..., ge=0, description="Number of Aye votes.")
    noes_count: int = Field(..., ge=0, description="Number of Noe votes.")
    motion_text: str | None = Field(None, description=(
        "The motion being voted on (TextBeforeVote), e.g. 'Division on Motion A1'. "
        "This is the citable description of what the division decided."
    ))
    result_text: str | None = Field(None, description="The result statement (TextAfterVote), e.g. 'Motion A1 disagreed.'")
    debate_section: str | None = Field(None, description="Parent debate title (DebateSection), e.g. 'Renters' Rights Bill'.")
    debate_section_ext_id: str | None = Field(None, description=(
        "Parent debate GUID (DebateSectionExtId). Use as {debate_ext_id} in "
        "hansard://debate/{debate_ext_id}/header to read the surrounding debate context."
    ))


class GetDebateDivisionsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    debate_ext_id: str = Field(..., description=(
        "Debate GUID (DebateSectionExtId). Chain from parliament_search_hansard "
        "contribution.debate_ext_id, top_debates[].debate_ext_id, or "
        "parliament_policy_position_summary top_debates[].debate_ext_id."
    ), min_length=8)


class DebateDivisions(BaseModel):
    """Divisions held within a specific debate.

    Empty `divisions` is the normal case — most debates contain no formal vote
    (Business of the House, statements, urgent questions, debates without a
    division). A populated list typically appears around bill stages, motions,
    and contested amendments.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    debate_ext_id: str = Field(..., description="Echo of the input debate GUID.")
    divisions: list[DivisionMatchLite] = Field(
        default_factory=list,
        description=(
            "Divisions held in this debate, in chronological order. Empty when no "
            "divisions occurred. Each element's `id` chains to votes_get_division."
        ),
    )


class LookupByColumnInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    column_number: str = Field(..., description=(
        "Hansard column number from an OSCOLA footnote, e.g. '200' for "
        "'HL Deb 14 Oct 2025, vol 849, col 200'. String (not integer) to "
        "accommodate column suffixes like '1162W' for written answers."
    ), min_length=1, max_length=20)
    volume_number: int = Field(..., description=(
        "Hansard volume number (the 'vol 849' part of an OSCOLA citation). "
        "Required — the endpoint only resolves citations when given the volume; "
        "sitting date is NOT a substitute (verified live 2026-05-29)."
    ), gt=0)
    house: Literal["Commons", "Lords", "both"] = Field("both", description=(
        "Restrict to one House. Default 'both' searches across both Houses."
    ))


class ColumnLookupResult(BaseModel):
    """Result of looking up a Hansard citation by (volume, column).

    Each match is a debate section that contains the cited column. The lawyer
    then drills into `debate_ext_id` via hansard://debate/{ext_id}/header for
    the ordered contribution index, picking the contribution at the cited
    column to footnote.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    column_number: str = Field(..., description="Echo of the requested column number.")
    volume_number: int = Field(..., description="Echo of the requested volume number.")
    house: Literal["Commons", "Lords", "both"] = Field(..., description="House filter applied.")
    total_results: int = Field(..., ge=0, description="Number of debate matches found.")
    matches: list[TopDebate] = Field(
        default_factory=list,
        description=(
            "Debate sections containing the cited column, in upstream relevance "
            "order. Each element's `debate_ext_id` chains to "
            "hansard://debate/{debate_ext_id}/header, and carries `source`/"
            "`source_code` for the citation's publication state. Resolution is "
            "NOT gated on publication state — Daily Part, Bound Volume, and "
            "Historic columns all resolve. Empty matches typically mean the "
            "volume number is wrong (running-volume vs bound-volume number), the "
            "column is a Written Answer/Statement needing its suffix (e.g. "
            "'1162W'), or a very recent column not yet indexed upstream."
        ),
    )


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
        "Top 20 debates ranked by upstream relevance_rank, with debate_ext_id "
        "for hansard://debate/{debate_ext_id}/header drill-down. "
        "contribution_count is null in this preview shape (would require a "
        "secondary call per debate)."
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


# Resolve forward references on HansardSearchResult.top_debates and
# .top_divisions (TopDebate / DivisionMatchLite are defined later in this file).
HansardSearchResult.model_rebuild()
