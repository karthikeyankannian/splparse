"""Reference data: known SPL commands and eval/stats functions."""
from __future__ import annotations

# Commands that share the stats grammar: aggregations [AS alias] [BY fields]
STATS_FAMILY = {
    "stats", "tstats", "eventstats", "streamstats", "chart", "timechart",
    "sistats", "sichart", "sitimechart", "mstats", "geostats",
}

SEARCH_COMMANDS = {"search"}

# Generating commands (valid as the first command without a leading "|")
GENERATING = {
    "search", "tstats", "inputlookup", "inputcsv", "makeresults", "metadata",
    "rest", "dbinspect", "datamodel", "from", "loadjob", "mstats", "gentimes",
    "savedsearch", "pivot", "mcatalog", "eventcount", "multisearch", "set",
}

KNOWN_COMMANDS = {
    # core
    "search", "where", "eval", "stats", "tstats", "eventstats", "streamstats",
    "chart", "timechart", "table", "fields", "rename", "sort", "head", "tail",
    "dedup", "top", "rare", "rex", "regex", "lookup", "inputlookup", "outputlookup",
    "join", "append", "appendcols", "appendpipe", "transaction", "bin", "bucket",
    "makeresults", "fillnull", "filldown", "mvexpand", "mvcombine", "spath",
    "xpath", "kv", "extract", "convert", "replace", "collect", "summaryindex",
    "sistats", "sichart", "sitimechart", "sirare", "sitop", "metadata", "rest",
    "datamodel", "from", "pivot", "inputcsv", "outputcsv", "loadjob", "savedsearch",
    "map", "multisearch", "set", "union", "return", "format", "localop", "noop",
    "addinfo", "addtotals", "addcoltotals", "accum", "delta", "autoregress",
    "trendline", "predict", "anomalies", "anomalydetection", "cluster", "kmeans",
    "outlier", "untable", "xyseries", "transpose", "reverse", "iplocation",
    "geom", "geostats", "gauge", "highlight", "abstract", "diff", "erex",
    "typer", "typeahead", "tags", "audit", "history", "cofilter", "correlate",
    "contingency", "associate", "arules", "concurrency", "eventcount",
    "fieldsummary", "fieldformat", "foreach", "gentimes", "iconify", "localize",
    "makecontinuous", "makemv", "nomv", "mstats", "mcatalog", "mcollect",
    "meventcollect", "multikv", "relevancy", "reltime", "rangemap", "require",
    "rtorder", "run", "script", "scrub", "searchtxn", "selfjoin", "sendemail",
    "sendalert", "setfields", "strcat", "streamstats", "tscollect", "x11",
    "walklex", "xmlkv", "xmlunescape", "dbinspect", "delete", "findtypes",
    "folderize", "input", "overlap", "mpreview", "msearch", "redistribute",
    "rare", "timewrap", "tojson", "fromjson", "reduce", "dump", "rename",
}

EVAL_FUNCTIONS = {
    # comparison / conditional
    "case", "cidrmatch", "coalesce", "false", "if", "in", "like", "lookup",
    "match", "null", "nullif", "searchmatch", "true", "validate",
    # conversion
    "ipmask", "printf", "tonumber", "tostring",
    # crypto
    "md5", "sha1", "sha256", "sha512",
    # date/time
    "now", "relative_time", "strftime", "strptime", "time",
    # informational
    "isbool", "isint", "isnotnull", "isnull", "isnum", "isstr", "typeof",
    # json
    "json_object", "json_append", "json_array", "json_array_to_mv", "json_extend",
    "json_extract", "json_extract_exact", "json_keys", "json_set", "json_set_exact",
    "json_valid", "json_delete",
    # math
    "abs", "ceiling", "ceil", "exact", "exp", "floor", "ln", "log", "pi", "pow",
    "round", "sigfig", "sqrt", "sum",
    # multivalue
    "commands", "mvappend", "mvcount", "mvdedup", "mvfilter", "mvfind",
    "mvindex", "mvjoin", "mvmap", "mvrange", "mvsort", "mvzip", "mv_to_json_array",
    "split",
    # statistical
    "avg", "max", "min", "random",
    # text
    "len", "lower", "ltrim", "replace", "rtrim", "spath", "substr", "trim",
    "upper", "urldecode",
    # trig
    "acos", "acosh", "asin", "asinh", "atan", "atan2", "atanh", "cos", "cosh",
    "hypot", "sin", "sinh", "tan", "tanh",
    # misc
    "strcat", "bit_and", "bit_or", "bit_xor", "bit_not", "bit_shift_left",
    "bit_shift_right", "object_to_array",
}

STATS_FUNCTIONS = {
    "avg", "count", "dc", "distinct_count", "earliest", "earliest_time",
    "estdc", "estdc_error", "exactperc", "first", "last", "latest", "latest_time",
    "list", "max", "mean", "median", "min", "mode", "p", "perc", "percentile",
    "range", "rate", "rate_avg", "rate_sum", "stdev", "stdevp", "sum", "sumsq",
    "upperperc", "values", "var", "varp", "eval", "c", "sparkline", "per_day",
    "per_hour", "per_minute", "per_second", "estdc",
}
