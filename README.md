# splparse

![CI](https://github.com/Karthikeyankannian/splparse/actions/workflows/ci.yml/badge.svg)

A parser, AST, formatter and linter for Splunk's **Search Processing Language (SPL)** — the
`Microsoft.Azure.Kusto.Language` equivalent that Splunk never shipped. Pure Python, zero
dependencies, Python 3.10+.

```python
from splparse import parse, format, lint, analysis

q = parse('index=web status>=500 | stats count by host | where count > 10')

q.commands[1].name                      # 'stats'
q.commands[1].aggregations[0].func      # Field(name='count')
q.commands[1].by.fields[0].name         # 'host'
q.commands[2].expr.op                   # '>'

print(format(q))
# index=web status>=500
# | stats count BY host
# | where count > 10

analysis.indexes(q)                      # {'web'}
analysis.referenced_fields(q)            # {'index', 'status', 'host', 'count'}
for d in lint(q): print(d)
```

## Why

Anyone building tooling around SPL — linters, IDE plugins, SPL↔KQL translators,
detection-as-code pipelines, searches-inventory scripts, AI agents that write searches — has
to hand-roll a grammar. This is that grammar, with a stable AST you can walk, rewrite and
print back out.

## What you get

| Module | Purpose |
|---|---|
| `splparse.tokenize` | Lexer with line/column positions; handles strings, `'quoted fields'`, `` `macros(args)` ``, ``` ``` comments ``` ```, time modifiers (`-24h@h`), wildcards, `<<FIELD>>` templates |
| `splparse.parse` | Full pipeline parser: implicit leading `search`, subsearches `[...]`, macro commands, 150+ known commands |
| `splparse.nodes` | Typed dataclass AST with `walk()`, `children()`, `to_dict()` (JSON) and source `span`s |
| `splparse.format` / `to_spl` | Canonical pretty-printer; `parse(format(x))` is a fixed point |
| `splparse.lint` | Rule engine (unknown commands/functions, leading wildcards, `index=*`, expensive `join`/`transaction`, …) — add your own with `@rule` |
| `splparse.analysis` | `indexes()`, `referenced_fields()`, `commands_used()`, `search_filters()` |
| `splparse` CLI | `splparse parse|fmt|lint|tokens "<spl>"` (or `-` for stdin) |
| `splparse-mcp` | MCP server exposing the above as tools for Claude Desktop / Claude Code / Cursor (see below) |

### Dedicated grammars

`search`, `where`, `eval`, the stats family (`stats tstats eventstats streamstats chart timechart
sistats mstats …` with `AS`, `BY`, `span=`, `from datamodel=`, `where`), `fields`, `table`,
`rename`, `sort`, `head`, `rex`, `lookup`, `dedup`, `top`/`rare`.

Every other command (`join`, `append`, `transaction`, `bin`, `inputlookup`, `makeresults`, `fillnull`,
`spath`, `foreach`, `collect`, …) is parsed by a generic grammar into `key=value` options,
positional arguments (with optional `AS`), `BY` clauses and subsearches — so the whole
pipeline is always structured, never a string blob.

### Expressions

`eval`/`where` expressions use a Pratt parser with Splunk precedence
(`OR` < `AND` < `NOT` < comparisons/`LIKE`/`IN` < `.` < `+ -` < `* / %` < unary). Search
expressions get their own grammar: implicit `AND`, uppercase `AND`/`OR`/`NOT`, `field IN (…)`,
`TERM()`/`CASE()`, phrases, wildcards and comparisons.

## Install

```bash
pip install git+https://github.com/Karthikeyankannian/splparse.git
# or, for development:
git clone https://github.com/Karthikeyankannian/splparse.git && cd splparse
pip install -e ".[dev]" && pytest
```

## Examples

```bash
splparse fmt 'index=main | stats count by host | sort -count'
splparse lint '*error | join host [search index=cmdb]'
splparse parse --compact '| tstats count from datamodel=Web where Web.status=404 by Web.src' | jq .
```

Rewrite an AST and print it:

```python
from splparse import parse, to_spl, nodes as N

q = parse('index=web | stats count by host')
for n in q.walk():
    if isinstance(n, N.Field) and n.name == 'host':
        n.name = 'dest_host'
print(to_spl(q))   # index=web | stats count BY dest_host
```

Add a lint rule:

```python
from splparse.lint import rule, Diagnostic
from splparse import nodes as N

@rule
def no_table_before_stats(q):
    names = [c.name for c in q.commands]
    if 'table' in names and 'stats' in names and names.index('table') < names.index('stats'):
        return [Diagnostic('table-before-stats', 'use fields instead of table before stats', 'warning')]
    return []
```

## MCP server — validate SPL before Splunk runs it

`splparse-mcp` turns the library into an [MCP](https://modelcontextprotocol.io) server with six tools:
`validate_spl`, `lint_spl`, `format_spl`, `parse_spl`, `analyze_spl`, `list_spl_commands`.
Pair it with Splunk's own MCP server (Splunkbase app 7931, which exposes `run_splunk_query`) and an
assistant can check and lint a generated search *before* executing it.

```bash
pip install "splparse[mcp]"
splparse-mcp                 # stdio (what desktop clients expect)
splparse-mcp --http 8765     # streamable HTTP at http://localhost:8765/mcp
```

Claude Desktop / Claude Code (`claude_desktop_config.json` or `.mcp.json`):

```json
{
  "mcpServers": {
    "splparse": { "command": "splparse-mcp" }
  }
}
```

Works with MCP Python SDK 1.x and 2.x.

## Known limitations (v0.1)

- SPL is context-sensitive at the lexical level (`a-b` is one term in `search` but a subtraction in
  `eval`). The lexer favours search semantics and the expression parser re-splits glued arithmetic;
  write `a - b` with spaces in eval if you hit an edge case.
- Macro bodies are not expanded (there is no macros.conf access); they are kept as `MacroCall` nodes.
- Per-command argument validation (which options a command accepts) is not yet modelled — the
  registry in `splparse/registry.py` is the place to add it.
- No semantic layer (field types, data model resolution) yet.

## Roadmap ideas

Command option schemas • SPL→KQL/Sigma translation on top of the AST • Language Server (LSP)
for VS Code • `splunk-sdk` integration to validate against a live instance • Tree-sitter grammar
generated from the same tests.

Licensed under Apache-2.0.
