# Tool Reference

This is the compact public reference for the uk-legal-mcp tool surface. Tool descriptions inside MCP clients carry more detailed routing guidance.

The tables are generated from the live server (`fastmcp inspect`) by `scripts/gen_reference.py`; edit the prose, not the tables. CI fails when they drift.

## Case Law

<!-- BEGIN GENERATED: tools prefix=case_law_,judgment_ -->
| Tool | Title | Summary |
|---|---|---|
| `judgment_get_header` | Get Judgment Header | Use when you have a judgment slug and need metadata (parties, judges, neutral citation, court, dates). |
| `judgment_get_index` | Get Judgment Paragraph Index | Use when you have a judgment slug and want the paragraph navigation index (eId + preview line for every paragraph). |
| `judgment_get_paragraph` | Get Judgment Paragraph | Use when you have a judgment slug + LegalDocML eId and want that paragraph's full text. |
| `case_law_search` | Search UK Case Law | Use when searching UK case law by party names, court, judge, date, or free-text query. |
| `case_law_grep_judgment` | Search within a UK Court Judgment | Use when you have a judgment slug and want to find paragraphs whose text matches a pattern. |
<!-- END GENERATED -->

<!-- BEGIN GENERATED: templates prefix=judgment:// -->
| Resource | Name | Summary |
|---|---|---|
| `judgment://{slug*}/header` | UK Court Judgment — metadata header | Metadata for a TNA judgment: parties, judges, neutral citation, court, dates. ~1,000 tokens. |
| `judgment://{slug*}/index` | UK Court Judgment — paragraph index | Navigation index: 'eId: first_line' rows for every paragraph in the judgment (~4,000 tokens for a typical Supreme Court case). |
| `judgment://{slug*}/para/{eId}` | UK Court Judgment — single paragraph | A single &lt;paragraph> element by its LegalDocML eId (e.g. 'para_12'). |
<!-- END GENERATED -->

## Legislation

<!-- BEGIN GENERATED: tools prefix=legislation_ -->
| Tool | Title | Summary |
|---|---|---|
| `legislation_search` | Search UK Legislation | Use when searching UK Acts and Statutory Instruments by title, phrase, or full-text. |
| `legislation_get_section` | Get Legislation Section | Use when you have a known Act / SI and want the parsed text of a specific section, regulation, or article, with extent and in-force metadata. |
| `legislation_get_toc` | Get Legislation Table of Contents | Use when you have a known Act / SI and want the structural table of contents (parts, chapters, individual sections/regulations/articles). |
<!-- END GENERATED -->

<!-- BEGIN GENERATED: templates prefix=legislation:// -->
| Resource | Name | Summary |
|---|---|---|
| `legislation://{type}/{year}/{number}/section/{section}{?date}` | UK Legislation Section (CLML XML) | CLML XML for a specific section of an Act or SI. |
| `legislation://{type}/{year}/{number}/toc{?date}` | UK Legislation Table of Contents | Flat table of contents (parts, chapters, sections, schedules) as a newline-separated 'id: title' list. |
<!-- END GENERATED -->

## Parliament

<!-- BEGIN GENERATED: tools prefix=parliament_ -->
| Tool | Title | Summary |
|---|---|---|
| `parliament_search_hansard` | Search Hansard Debates | Use when searching Hansard by topic, bill title, or text phrase. |
| `parliament_policy_position_summary` | Hansard Policy Position Summary (deterministic facets) | Use when you want debate-level corpus signals on a topic — by_house, by_year, by_section breakdowns — without reading every contribution. |
| `parliament_find_member` | Find Member of Parliament | Use when you have a member's name and need their integer member_id. |
| `parliament_member_debates` | Get Member Debates | Use when you have a member_id and want contributions where THAT member used a specific topic phrase verbatim (text-body search). |
| `parliament_member_interests` | Get Member Financial Interests | Use when you have a member_id and need their registered financial interests (donations, directorships, land, gifts). |
| `parliament_search_petitions` | Search UK Parliament Petitions | Use when searching UK Parliament petitions by keyword or topic. |
| `parliament_get_debate_divisions` | Get Divisions Held In A Debate | Use when you have a debate_ext_id and want the divisions (formal votes) held within it. |
| `parliament_get_debate_contributions` | Get Contributions In A Debate | Use when you have a debate_ext_id and want verbatim contributions, optionally filtered to one member. |
| `parliament_lookup_by_column` | Resolve A Hansard Column Citation | Use when you have an OSCOLA-style Hansard citation (column + volume + house) and need the debate. |
<!-- END GENERATED -->

<!-- BEGIN GENERATED: templates prefix=hansard:// -->
| Resource | Name | Summary |
|---|---|---|
| `hansard://debate/{debate_ext_id}/header` | Hansard Debate — header + ordered contribution index | Debate overview (title, date, house, location, volume) plus an ordered index of contributions in the debate: 'OrderInSection: AttributedTo [col_ref] — first 100 chars'. |
| `hansard://debate/{debate_ext_id}/contribution/{contribution_ext_id}` | Hansard Contribution — single contribution full text | A single contribution's full text + member metadata + column reference, extracted from its parent debate. |
| `hansard://member/{member_id}/biography` | UK Parliament Member — biography | Full biography from the Members API: constituency representations, house memberships, government posts, opposition posts, committee memberships, party affiliations. |
<!-- END GENERATED -->

## Bills

<!-- BEGIN GENERATED: tools prefix=bills_ -->
| Tool | Title | Summary |
|---|---|---|
| `bills_search_bills` | Search Parliamentary Bills | Use when searching UK parliamentary bills by keyword, session, house, or legislative stage. |
| `bills_get_bill` | Get Bill Detail | Use when you have a bill_id (from bills_search_bills) and want the full detail. |
<!-- END GENERATED -->

## Votes

<!-- BEGIN GENERATED: tools prefix=votes_ -->
| Tool | Title | Summary |
|---|---|---|
| `votes_search_divisions` | Search Parliamentary Divisions | Use when searching Commons or Lords formal votes by topic, date, or member. |
| `votes_get_division` | Get Division Detail | Use when you have a division_id + house and want the full member-by-member voting record. |
<!-- END GENERATED -->

## Committees

<!-- BEGIN GENERATED: tools prefix=committees_ -->
| Tool | Title | Summary |
|---|---|---|
| `committees_search_committees` | Search Parliamentary Committees | Use when searching or listing UK parliamentary select committees by name, house, or active status. |
| `committees_get_committee` | Get Committee Detail | Use when you have a committee_id and want the metadata + current membership. |
| `committees_search_businesses` | Search Committee Inquiries and Business | Use when identifying a committee inquiry or business before evidence retrieval. |
| `committees_search_evidence` | Search Committee Evidence | Use when retrieving committee evidence, especially for an identified inquiry. |
<!-- END GENERATED -->

## Citations

<!-- BEGIN GENERATED: tools prefix=citations_ -->
| Tool | Title | Summary |
|---|---|---|
| `citations_parse` | Parse OSCOLA Citations | Use when you have free text (a memo, an email, a clause) and want every OSCOLA-style citation it contains extracted and classified. |
| `citations_resolve` | Resolve Single OSCOLA Citation | Use before constructing an OSCOLA citation string from known fields, OR to confirm a citation points at a real document. |
| `citations_network` | Get Case Citation Network | Use when you have a judgment slug and want to map every citation it makes — cases cited, legislation referenced, SIs, retained EU law. |
| `citations_format_oscola` | Format OSCOLA Citation String | Use after citations_resolve to produce the correctly formatted OSCOLA citation string. |
<!-- END GENERATED -->

Supported citation formats include neutral citations, law reports, legislation sections, Statutory Instruments, and retained EU law.

## HMRC

<!-- BEGIN GENERATED: tools prefix=hmrc_ -->
| Tool | Title | Summary |
|---|---|---|
| `hmrc_get_vat_rate` | Get VAT Rate for Commodity | Use when you know the UK VAT category name for a good or service and want its VAT treatment. |
| `hmrc_search_guidance` | Search HMRC Guidance | Use when discovering HMRC GOV.UK guidance on a topic. |
| `hmrc_get_manual_page` | Read HMRC Internal Manual Page | Use when a cited HMRC internal-manual page must be read. |
<!-- END GENERATED -->

`hmrc_check_mtd_status` is not offered on the hosted instance, which has only HMRC sandbox credentials. A self-hosted instance offers it when `HMRC_API_BASE=https://api.service.hmrc.gov.uk` is set, with production `HMRC_CLIENT_ID` and `HMRC_CLIENT_SECRET`.

## Resource bridge and generated tools

<!-- BEGIN GENERATED: tools prefix=read_resource,list_resources,list_prompts,get_prompt -->
| Tool | Title | Summary |
|---|---|---|
| `list_prompts` | list_prompts | List all available prompts. |
| `get_prompt` | get_prompt | Get a prompt by name with optional arguments. |
| `list_resources` | list_resources | List all available resources and resource templates. |
| `read_resource` | read_resource | Read a registered server resource URI, not an ordinary web URL. |
<!-- END GENERATED -->

<!-- BEGIN GENERATED: resources -->
| Resource | Name | Summary |
|---|---|---|
| `server://about` | About this server | Provenance, upstream APIs, and operational posture. |
<!-- END GENERATED -->

`read_resource` reads only the `judgment://`, `legislation://`, `hansard://`, and `server://` resource URIs exposed by this server. It does not fetch ordinary `https://` URLs. Use a source-specific tool for a cited web URL; canonical GOV.UK HMRC internal-manual URLs are retrieved with `hmrc_get_manual_page`.

## Prompts

Workflow templates are exposed as tools via `PromptsAsTools` for clients that cannot consume MCP prompts natively.

<!-- BEGIN GENERATED: prompts -->
| Prompt | Summary |
|---|---|
| `legislation_summarise_act` | Summarise a UK Act of Parliament or Statutory Instrument. |
| `legislation_compare_legislation` | Compare two pieces of UK legislation on a specific topic. |
| `parliament_policy_reception_review` | Review how a policy topic is being received in Parliament, with citable evidence. |
| `parliament_member_record_on_topic` | Assemble the citable record of a parliamentarian's contributions on a topic. |
<!-- END GENERATED -->
