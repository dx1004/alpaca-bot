# Strategy evidence files

`source-registry.yaml` records each cited document with a unique uppercase
`source_id`, source kind, URL, quoted ISO access/publication dates and the exact
strategy versions to which it applies. A missing publication date is `null`;
do not substitute the access date.

`strategy-versions/*.yaml` identifies one account stage and whether the version
is still a `CANDIDATE` or has been `FROZEN`. The current version is a research
candidate with blocked rule records, not an approved Ross rule set. A source's
`version_ids` entry for this candidate means it was considered in this research
bundle; it does not prove applicability to the $500/1x account stage.

`fidelity-matrix.yaml` contains rules for one version only. Each behavior has
a `rule_id`, domain, statement, source IDs and exactly one classification:

- `ROSS_DIRECT` requires a Ross first-party source.
- `BROKER_EQUIVALENT` requires Ross and Alpaca official sources plus written
  equivalence evidence.
- `ENGINEERING_CONTROL` requires a non-strategy engineering basis.
- `BLOCKED_RESEARCH` requires a blocking reason and cannot authorize exposure.

The loader in `src/alpaca_ross_bot/evidence.py` rejects unknown fields,
unrecognized classifications, duplicate IDs, missing sources and cross-version
citations. Add a source's actual date and version scope before using it to
support a rule. Candidate records grant no permission to trade.
