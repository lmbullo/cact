"""driver_args: the typed-call argument boundary — DRIVER fragment 2/4.

WHAT IT IS
  Raw child source `_ARGS`: `_USE_FALLBACK`/`_USE_BY_CODE`, `_error_shape`, `_BadArg`, `_scalar`,
  `_coerce` and `_coerce_args`, the schema-directed argument coercion every typed call crosses.
  driver.py concatenates it before the stdin loop.

ENTRY POINTS
  _ARGS   source text; inside: _USE_FALLBACK, _USE_BY_CODE, _error_shape, _BadArg, _scalar,
          _coerce, _coerce_args

INVARIANTS
  fragment, not an API: a bad keyword/value stays a structured bad_arguments refusal with the
  same `use` text; coercion never raises out of the loop.

WHERE TO CHANGE
  value coercion ...... _scalar, _coerce, _coerce_args
  refusal shape ....... _error_shape, _USE_BY_CODE
"""
_ARGS = r'''_USE_FALLBACK = "call help(tool) for the signature and the accepted arguments"
_USE_BY_CODE = {
    "outside_root": "typed editors are workspace-only; use exec(shell=...) for a scratch path",
    "not_found": "map(path) lists files; search(pattern) finds text",
    "unknown_verb": "help() lists every verb; help(name) matches a name",
    "bad_arguments": "call help(tool) for the accepted keywords and signature",
}


def _error_shape(value, tool, signature=None):
    # The one refusal shape, applied at the boundary every typed call goes through. A verb may
    # answer a bare {code, path} or put `reason` at the top level for python callers; the model
    # only ever sees {ok:false, kind, error:{code,message,use,signature}, path?}.
    error = value.get("error") if isinstance(value.get("error"), dict) else {}
    kind = str(value.get("kind") or "")
    if not kind or kind == tool:
        kind = tool + "_failed"
    code = str(error.get("code") or value.get("code") or kind)
    message = str(error.get("message") or value.get("reason") or value.get("message") or code)
    use = str(error.get("use") or value.get("use") or _USE_BY_CODE.get(code) or _USE_FALLBACK)
    shaped = {"code": code, "message": message, "use": use}
    for key in ("signature", "valid", "available", "argument"):
        if error.get(key) is not None:
            shaped[key] = error[key]
        elif value.get(key) is not None:
            shaped[key] = value[key]
    if signature and not shaped.get("signature"):
        shaped["signature"] = "%s%s" % (tool, signature)
    out = {key: item for key, item in value.items()
           if key not in ("error", "reason", "use", "code", "message")}
    out["ok"] = False
    out["kind"] = kind
    out["error"] = shaped
    if out.get("path") is None and error.get("path") is not None:
        out["path"] = error["path"]
    return out


class _BadArg(Exception):
    def __init__(self, name, value, expected):
        self.name = name
        self.value = value
        self.expected = expected


def _scalar(value, kind, name):
    # Coerce/validate the scalar types a schema declares. This is the boundary, not a verb:
    # every typed call gets the same answer for a bad integer/number/boolean/string.
    if value is None:
        return value
    if kind == "integer":
        if isinstance(value, bool):
            raise _BadArg(name, value, "an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip(), 10)
            except (TypeError, ValueError):
                pass
        raise _BadArg(name, value, "an integer")
    if kind == "number":
        if isinstance(value, bool):
            raise _BadArg(name, value, "a number")
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                number = float(value.strip())
            except (TypeError, ValueError):
                raise _BadArg(name, value, "a number") from None
            return int(number) if number.is_integer() else number
        raise _BadArg(name, value, "a number")
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in ("true", "1", "yes", "on"):
                return True
            if text in ("false", "0", "no", "off"):
                return False
        raise _BadArg(name, value, "a boolean")
    if kind == "string":
        if isinstance(value, str):
            return value
        raise _BadArg(name, value, "a string")
    return value


def _coerce(value, schema, name):
    # Recursively coerce properties, array items and oneOf/anyOf branches. An array keeps a
    # string when its items are strings: exec(argv="...") is the verb's own shell-string path.
    if not isinstance(schema, dict):
        return value
    branches = schema.get("oneOf") or schema.get("anyOf")
    if branches:
        last = None
        for branch in branches:
            try:
                return _coerce(value, branch, name)
            except _BadArg as exc:
                last = exc
        if last is not None:
            raise last
        return value
    kind = schema.get("type")
    if kind == "object" and isinstance(value, dict):
        props = schema.get("properties") or {}
        for key in list(value):
            if key in props:
                value[key] = _coerce(value[key], props[key], key)
        return value
    if kind == "array":
        items = schema.get("items") if isinstance(schema.get("items"), dict) else None
        if isinstance(value, (list, tuple)):
            if items is None:
                return list(value)
            return [_coerce(item, items, name) for item in value]
        if isinstance(value, str):
            if isinstance(items, dict) and items.get("type") == "string":
                return value
            raise _BadArg(name, value, "a list")
        raise _BadArg(name, value, "a list")
    return _scalar(value, kind, name)


def _coerce_args(tool, args, meta, signature):
    schema = (meta.get(tool) or {}).get("schema") or {}
    props = schema.get("properties") or {}
    coerced = dict(args)
    for key, item in list(coerced.items()):
        if key in props:
            coerced[key] = _coerce(item, props[key], key)
    return coerced

'''
