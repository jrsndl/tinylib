"""Cached parsing and JSON-value matching for explicit {field}:term queries."""
import json
import re
import shlex
from functools import lru_cache

FIELD_FILTER = re.compile(r"""(?<!\S)(-?)\{([^{}\r\n]+)\}\s*([:!])\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s{}"']+)""")


def field_name(value):
    return re.sub(r'[\s_]+', '_', value.strip().casefold())


@lru_cache(maxsize=128)
def parse_query(query):
    clauses = []
    def extract(match):
        excluded, key, operator, term = match.groups()
        if term.startswith(('"', "'")):
            term = shlex.split(term)[0]
        clauses.append((field_name(key), term.casefold(), bool(excluded) ^ (operator == '!')))
        return ' '
    plain = FIELD_FILTER.sub(extract, query)
    return plain, tuple(clauses)


def field_values(document, target, path=()):
    """Match a leaf key anywhere, or a full dotted path, including nested lists."""
    if isinstance(document, dict):
        for key, value in document.items():
            name = field_name(str(key))
            parts = path + (name,)
            if name == target or '.'.join(parts) == target:
                yield value
            yield from field_values(value, target, parts)
    elif isinstance(document, list):
        for value in document:
            yield from field_values(value, target, path)


def matches_fields(asset, clauses):
    for key, term, excluded in clauses:
        found = any(term in (value if isinstance(value, str) else
                             json.dumps(value, ensure_ascii=False)).casefold()
                    for value in field_values(asset, key))
        if found == excluded:
            return False
    return True
