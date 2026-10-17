import json

import pytest

from splparse import SPLSyntaxError, format, lint, nodes as N, parse, parse_expression, to_spl, tokenize

CORPUS = [
    # plain searches
    'index=main sourcetype=access_combined status=500',
    'index=main (status=500 OR status=503) NOT host=dev*',
    'index="win" EventCode IN (4624, 4625) user!=SYSTEM earliest=-24h@h latest=now',
    'index=proxy TERM(10.0.0.1) "connection refused"',
    'search index=_internal log_level=ERROR',
    'source=/var/log/syslog* host=web-01',
    'index=main error-code=500 foo-bar',
    'index=main status>=500 bytes<1024',
    # stats family
    'index=web | stats count, dc(clientip) AS unique_ips, avg(bytes) AS avg_bytes by status, host',
    'index=web | stats count(eval(status>=500)) as errors, count as total by host',
    '| tstats summariesonly=t count from datamodel=Web.Web where Web.status=404 by Web.src span=1h',
    '| tstats count where index=main by _time span=5m',
    'index=web | timechart span=1h count by host limit=10 useother=f',
    'index=web | chart count over host by status',
    'index=web | eventstats max(bytes) as max_bytes by host | streamstats count as seq',
    'index=web | stats p95(response_time) as p95, perc99(response_time) as p99',
    # eval / where
    'index=web | eval kb = bytes / 1024, label = if(status >= 500, "err", "ok")',
    'index=web | eval x = a-b, y = x*2+1, z = -x',
    'index=web | eval msg = "status: " . status . " host=" . host',
    'index=web | eval d = strftime(_time, "%Y-%m-%d"), e = round(bytes/1024, 2)',
    "index=web | eval 'my field' = coalesce(src, src_ip), n = mvcount(values)",
    'index=web | where status >= 500 AND NOT like(host, "dev%")',
    'index=web | where isnull(user) OR user IN ("admin", "root")',
    'index=web | where cidrmatch("10.0.0.0/8", src_ip) AND bytes > avg_bytes',
    'index=web | eval sev = case(status>=500, "high", status>=400, "medium", true(), "low")',
    # field manipulation
    'index=web | fields - _raw, _time | fields + host',
    'index=web | table _time, host, status | rename host AS "Host Name", status AS code',
    'index=web | sort 100 -count, +host | sort -num(bytes)',
    'index=web | sort count desc | head 10 | tail 5',
    'index=web | head (status>400) keeplast=t',
    'index=web | dedup 2 host, user consecutive=t sortby -_time',
    'index=web | top limit=5 user by host | rare 3 status countfield=cnt',
    # rex / lookup
    'index=web | rex field=_raw "user=(?<user>\\w+)" | rex mode=sed "s/foo/bar/g"',
    'index=web | rex max_match=0 "(?<ip>\\d+\\.\\d+\\.\\d+\\.\\d+)"',
    'index=web | lookup local=t dnslookup clientip AS src OUTPUT clienthost AS src_host, ip',
    'index=web | lookup geo src_ip OUTPUTNEW country',
    # subsearches, macros, joins
    'index=web [search index=alerts | return 100 src_ip]',
    'index=web user IN ([inputlookup admins.csv | fields user])',
    'index=web `my_macro(arg1, "a, b")` `other` | `format_time`',
    'index=web | join type=left host [search index=cmdb | fields host, owner]',
    'index=web | append [search index=other | stats count]',
    '| inputlookup assets.csv where priority=high | fields ip',
    '| makeresults count=5 | eval n = random() % 10',
    'index=web | transaction user maxspan=5m startswith="login" endswith="logout"',
    'index=web | bin _time span=1h | fillnull value=0 bytes, count',
    'index=web | mvexpand values | spath input=payload output=user path=user.name',
    'index=web ``` this is a comment ``` | stats count',
    'index=web | foreach bytes* [eval <<FIELD>>_kb = <<FIELD>> / 1024]',
    'index=web | collect index=summary | outputlookup append=t results.csv',
]


@pytest.mark.parametrize("spl", CORPUS)
def test_parses(spl):
    q = parse(spl)
    assert q.commands
    # the AST must be JSON serialisable
    json.dumps(q.to_dict())


@pytest.mark.parametrize("spl", CORPUS)
def test_roundtrip_is_stable(spl):
    """print(parse(x)) must itself parse to an equal AST (idempotent formatting)."""
    q1 = parse(spl)
    text = to_spl(q1)
    q2 = parse(text)
    assert to_spl(q2) == text, text          # printing is idempotent
    assert parse(to_spl(q2)) == q2            # canonical form is a fixed point
    assert len(q1.commands) == len(q2.commands)


def test_implicit_search_and_terms():
    q = parse('index=main (status=500 OR status=503) NOT host=dev* "literal text"')
    s = q.commands[0]
    assert isinstance(s, N.SearchCommand) and s.implicit
    e = s.expr
    assert isinstance(e, N.Binary) and e.op == "AND" and e.implicit
    # left-assoc: ((index=main AND (OR)) AND NOT host) AND "literal"
    assert isinstance(e.right, N.Literal) and e.right.kind == "string"
    assert isinstance(e.left.right, N.Unary) and e.left.right.op == "NOT"
    assert isinstance(e.left.left.right, N.Binary) and e.left.left.right.op == "OR"


def test_stats_structure():
    q = parse('index=web | stats count, dc(clientip) AS unique_ips by status, host')
    st = q.commands[1]
    assert isinstance(st, N.StatsCommand)
    assert [a.alias for a in st.aggregations] == [None, "unique_ips"]
    assert isinstance(st.aggregations[1].func, N.FunctionCall)
    assert [f.name for f in st.by.fields] == ["status", "host"]


def test_tstats_pieces():
    q = parse('| tstats summariesonly=t count from datamodel=Web.Web where Web.status=404 by Web.src')
    st = q.commands[0]
    assert st.from_ == "Web.Web"
    assert isinstance(st.where, N.Binary) and st.where.op == "="
    assert st.options[0].key == "summariesonly"


def test_eval_precedence():
    e = parse_expression("a + b * c . d")
    assert e.op == "."
    assert e.left.op == "+" and e.left.right.op == "*"
    e = parse_expression("NOT a=1 AND b=2 OR c=3")
    assert e.op == "OR" and e.left.op == "AND" and isinstance(e.left.left, N.Unary)
    assert to_spl(parse_expression("(a+b)*c")) == "(a + b) * c"
    assert to_spl(parse_expression("a-(b-c)")) == "a - (b - c)"


def test_hyphen_resplit_in_eval():
    q = parse("| makeresults | eval x = a-b, t = relative_time(now(), \"-1d@d\")")
    ev = q.commands[1]
    assert isinstance(ev.assignments[0].value, N.Binary) and ev.assignments[0].value.op == "-"


def test_search_keeps_hyphenated_terms():
    q = parse("index=main error-code=500 foo-bar")
    terms = [n.name for n in q.commands[0].expr.walk() if isinstance(n, N.Field)]
    assert "error-code" in terms
    lits = [n.value for n in q.commands[0].expr.walk() if isinstance(n, N.Literal)]
    assert "foo-bar" in lits


def test_subsearch_and_macro():
    q = parse('index=web [search index=alerts | return 100 src_ip] `my_macro(arg1, "a, b")`')
    e = q.commands[0].expr
    subs = [n for n in e.walk() if isinstance(n, N.Subsearch)]
    assert len(subs) == 1 and len(subs[0].pipeline.commands) == 2
    macs = [n for n in e.walk() if isinstance(n, N.MacroCall)]
    assert macs[0].name == "my_macro" and macs[0].args == ["arg1", '"a, b"']


def test_rename_sort_head():
    q = parse('index=web | rename host AS "Host Name" | sort 100 -count, +host | head 10')
    rn, so, hd = q.commands[1:]
    assert rn.pairs[0].target == "Host Name"
    assert so.limit == 100 and so.keys[0].descending and not so.keys[1].descending
    assert hd.count == 10


def test_lookup():
    q = parse('index=web | lookup local=t dnslookup clientip AS src OUTPUT clienthost AS src_host, ip')
    lk = q.commands[1]
    assert lk.table == "dnslookup" and lk.options[0].key == "local"
    assert lk.input_fields[0].target == "src"
    assert [p.target for p in lk.output_fields] == ["src_host", "ip"]
    assert lk.output_mode == "OUTPUT"


def test_format_multiline():
    out = format("index=web | stats count by host | sort -count")
    assert out.splitlines() == ["index=web", "| stats count BY host", "| sort -count"]


def test_span_and_raw():
    src = "index=web | stats count by host"
    q = parse(src)
    st = q.commands[1]
    assert src[st.span[0]:st.span[1]] == "stats count by host" == st.raw


def test_syntax_errors():
    with pytest.raises(SPLSyntaxError):
        parse("index=web | stats count by")
    with pytest.raises(SPLSyntaxError):
        parse('index=web "unterminated')
    with pytest.raises(SPLSyntaxError):
        parse("index=web | eval x = ")
    with pytest.raises(SPLSyntaxError):
        parse("index=web | where a = (1 + 2")
    with pytest.raises(SPLSyntaxError):
        parse("index=web | ")
    err = None
    try:
        parse("index=web | eval x = if(a,b")
    except SPLSyntaxError as e:
        err = e
    assert err is not None and err.position.column > 0


def test_lint_rules():
    d = {x.rule for x in lint("*error | statz count | join host [search a]")}
    assert {"leading-wildcard", "unknown-command", "expensive-join", "missing-index"} <= d
    d = {x.rule for x in lint("index=web | eval x = foo(1) | stats countt(x)")}
    assert {"unknown-eval-function", "unknown-stats-function"} <= d
    assert not [x for x in lint("index=web | stats count by host") if x.severity == "error"]


def test_tokens_positions():
    toks = tokenize('index=main\n| stats count')
    pipe = [t for t in toks if t.text == "|"][0]
    assert (pipe.start.line, pipe.start.column) == (2, 1)


def test_analysis_helpers():
    from splparse import analysis
    q = parse('index IN (a, b) sourcetype=x | stats count AS c by host | rename c AS total | join host [search index=cmdb]')
    assert analysis.indexes(q) == {"a", "b"}
    assert {"host", "c", "total", "sourcetype"} <= analysis.referenced_fields(q)
    assert analysis.commands_used(q) == ["search", "stats", "rename", "join", "search"]
    assert analysis.commands_used(q, include_subsearches=False) == ["search", "stats", "rename", "join"]
