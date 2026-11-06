import ast
import difflib
import io
import os
import re
import shutil
import tempfile
import tokenize

from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax
from rich.table import Table

console = Console()

M = "\ue000"

EXT_LANG = {
    ".js": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".css": "css",
    ".html": "html",
    ".htm": "html",
    ".py": "py",
    ".json": "json",
    ".jsonc": "jsonc",
    ".sh": "sh",
    ".bash": "sh",
    ".zsh": "sh",
    ".lua": "lua",
    ".qml": "qml",
    ".ts": "ts",
    ".mts": "ts",
    ".cts": "ts",
    ".c": "c",
    ".h": "c",
    ".cpp": "c",
    ".cc": "c",
    ".cxx": "c",
    ".hpp": "c",
    ".hh": "c",
    ".hxx": "c",
    ".java": "java",
    ".cs": "cs",
    ".go": "go",
    ".kt": "kt",
    ".kts": "kt",
    ".swift": "swift",
    ".rs": "rs",
}
LANGS = (
    "py",
    "js",
    "css",
    "html",
    "json",
    "jsonc",
    "sh",
    "lua",
    "qml",
    "ts",
    "c",
    "java",
    "cs",
    "go",
    "kt",
    "swift",
    "rs",
)
HEAD_LANGS = ("js", "sh", "lua", "qml", "ts")
LANG_KEEP = {
    "qml": re.compile(r"^//@"),
    "ts": re.compile(r"^///\s*<|@ts-(?:ignore|expect-error|nocheck|check)"),
    "go": re.compile(r"^//go:|^//\s*\+build|^//export\b|#cgo|#include"),
    "swift": re.compile(r"^//\s*swift-tools-version"),
}
SKIP_DIRS = {"node_modules", "__pycache__", "venv", "env", "dist", "build"}


def is_word(c):
    return bool(c) and (c.isalnum() or c in "_$")


class Out:
    """Output buffer that remembers the last real character written."""

    def __init__(self):
        self.parts = []
        self.last = ""

    def put(self, t):
        if t:
            self.parts.append(t)
            self.last = t[-1]

    def mark(self):
        self.parts.append(M)

    def text(self):
        return "".join(self.parts)


def after_block(out, src, j):
    """Tidy spacing after a removed block comment; returns new source index."""
    n = len(src)
    nxt = src[j] if j < n else ""
    if nxt in (" ", "\t") and out.last in ("", " ", "\t", "\n"):
        while j < n and src[j] in " \t":
            j += 1
    elif is_word(out.last) and is_word(nxt):
        out.parts.append(" ")
        out.last = " "
    return j


def skip_string(src, i):
    q = src[i]
    n = len(src)
    j = i + 1
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == q:
            return j + 1
        if c == "\n":
            return j
        j += 1
    return n


KEYWORDS = {
    "return",
    "typeof",
    "case",
    "do",
    "else",
    "in",
    "of",
    "void",
    "delete",
    "throw",
    "new",
    "instanceof",
    "yield",
    "await",
}
REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
RUST_CHAR = re.compile(
    r"'(?:\\(?:x[0-9A-Fa-f]{2}|u\{[0-9A-Fa-f_]{1,6}\}|[^xu])|[^\\'\n])'"
)


def strip_clike(
    src,
    keep,
    line_comments=True,
    templates=False,
    regex=False,
    single_quotes=True,
    cont=False,
    nested=False,
    triple=False,
    backtick_raw=False,
    rust=False,
    cpp_raw=False,
    verbatim=False,
):
    n = len(src)
    out = Out()
    st = {"last": "", "word": "", "adj": False}

    def sig(c):
        """Track the last significant character (for regex-vs-division)."""
        if c.isspace():
            st["adj"] = False
        elif is_word(c):
            st["word"] = st["word"] + c if st["adj"] else c
            st["adj"] = True
            st["last"] = c
        else:
            st["word"] = ""
            st["adj"] = False
            st["last"] = c

    def operand_end():
        st["last"], st["word"], st["adj"] = '"', "", False

    def regex_allowed():
        return st["last"] == "" or st["last"] in REGEX_PREV or st["word"] in KEYWORDS

    def read_regex(i):
        j = i + 1
        cls = False
        while j < n:
            c = src[j]
            if c == "\\":
                j += 2
                continue
            if c == "\n":
                return 0
            if cls:
                if c == "]":
                    cls = False
            elif c == "[":
                cls = True
            elif c == "/":
                j += 1
                while j < n and is_word(src[j]):
                    j += 1
                return j
            j += 1
        return 0

    def block_end(i):
        if not nested:
            j = src.find("*/", i + 2)
            return n if j < 0 else j + 2
        depth = 1
        j = i + 2
        while j < n and depth:
            if src.startswith("/*", j):
                depth += 1
                j += 2
            elif src.startswith("*/", j):
                depth -= 1
                j += 2
            else:
                j += 1
        return min(j, n)

    def line_end(i):
        j = src.find("\n", i)
        j = n if j < 0 else j
        while cont and j < n and src[j - 1] == "\\":
            k = src.find("\n", j + 1)
            j = n if k < 0 else k
        return j

    def triple_end(i):
        j = i + 3
        while j < n:
            if src[j] == "\\":
                j += 2
            elif src.startswith('"""', j):
                return j + 3
            else:
                j += 1
        return n

    def raw_end(i):
        if rust and src[i] in "rb" and (i == 0 or not is_word(src[i - 1])):
            k = i
            if src[k] == "b":
                k += 1
            if k >= n or src[k] != "r":
                return 0
            k += 1
            h = 0
            while k < n and src[k] == "#":
                h += 1
                k += 1
            if k >= n or src[k] != '"':
                return 0
            e = src.find('"' + "#" * h, k + 1)
            return n if e < 0 else e + 1 + h
        if cpp_raw and src[i] == "R" and src.startswith('"', i + 1):
            p = src.find("(", i + 2)
            if p < 0 or p - (i + 2) > 16:
                return 0
            delim = src[i + 2 : p]
            if any(ch in delim for ch in ' )\\\t\n"'):
                return 0
            e = src.find(")" + delim + '"', p + 1)
            return n if e < 0 else e + len(delim) + 2
        return 0

    def scan_template(i):
        while i < n:
            c = src[i]
            if c == "\\":
                out.put(src[i : i + 2])
                i += 2
            elif c == "`":
                out.put("`")
                return i + 1
            elif c == "$" and src.startswith("{", i + 1):
                out.put("${")
                st["last"], st["word"], st["adj"] = "", "", False
                i = scan_code(i + 2, True)
                if i < n:
                    out.put("}")
                    i += 1
            else:
                j = i + 1
                while j < n and src[j] not in "\\`$":
                    j += 1
                out.put(src[i:j])
                i = j
        return i

    def scan_code(i, nested):
        depth = 0
        while i < n:
            c = src[i]
            nxt = src[i + 1] if i + 1 < n else ""
            if c == "/" and nxt == "*":
                j = block_end(i)
                text = src[i:j]
                if keep(text):
                    out.put(text)
                else:
                    out.mark()
                    j = after_block(out, src, j)
                st["adj"] = False
                i = j
                continue
            if c == "/" and nxt == "/" and line_comments:
                j = line_end(i)
                text = src[i:j]
                if keep(text):
                    out.put(text)
                else:
                    out.mark()
                i = j
                continue
            if rust and c == "'":
                m = RUST_CHAR.match(src, i)
                if m:
                    out.put(m.group(0))
                    operand_end()
                    i = m.end()
                else:
                    out.put(c)
                    sig(c)
                    i += 1
                continue
            if c in "rbR":
                j = raw_end(i)
                if j:
                    out.put(src[i:j])
                    operand_end()
                    i = j
                    continue
            if verbatim and c == "@" and nxt == '"':
                j = i + 2
                while j < n:
                    if src.startswith('""', j):
                        j += 2
                    elif src[j] == '"':
                        j += 1
                        break
                    else:
                        j += 1
                j = min(j, n)
                out.put(src[i:j])
                operand_end()
                i = j
                continue
            if backtick_raw and c == "`":
                e = src.find("`", i + 1)
                j = n if e < 0 else e + 1
                out.put(src[i:j])
                operand_end()
                i = j
                continue
            if triple and src.startswith('"""', i):
                j = triple_end(i)
                out.put(src[i:j])
                operand_end()
                i = j
                continue
            if c == '"' or (c == "'" and single_quotes):
                j = min(skip_string(src, i), n)
                out.put(src[i:j])
                operand_end()
                i = j
                continue
            if c == "`" and templates:
                out.put("`")
                i = scan_template(i + 1)
                operand_end()
                continue
            if c == "/" and regex and regex_allowed():
                j = read_regex(i)
                if j:
                    out.put(src[i:j])
                    operand_end()
                    i = j
                    continue
            if nested:
                if c == "{":
                    depth += 1
                elif c == "}":
                    if depth == 0:
                        return i
                    depth -= 1
            out.put(c)
            sig(c)
            i += 1
        return i

    scan_code(0, False)
    return out.text()


def strip_js(src, keep):
    return strip_clike(src, keep, templates=True, regex=True)


def strip_css(src, keep):
    return strip_clike(src, keep, line_comments=False)


def strip_json(src, keep):
    return strip_clike(src, keep, single_quotes=False)


def strip_c(src, keep):
    return strip_clike(src, keep, cont=True, cpp_raw=True)


def strip_java(src, keep):
    return strip_clike(src, keep, triple=True)


def strip_cs(src, keep):
    return strip_clike(src, keep, triple=True, verbatim=True)


def strip_go(src, keep):
    return strip_clike(src, keep, backtick_raw=True)


def strip_kt(src, keep):
    return strip_clike(src, keep, nested=True, triple=True)


def strip_swift(src, keep):
    return strip_clike(src, keep, nested=True, triple=True, single_quotes=False)


def strip_rs(src, keep):
    return strip_clike(src, keep, nested=True, rust=True)


LUA_LONG_COMMENT = re.compile(r"--\[(=*)\[")
LUA_LONG_STRING = re.compile(r"\[(=*)\[")


def strip_lua(src, keep):
    n = len(src)
    out = Out()
    i = 0
    while i < n:
        c = src[i]
        if c == "-" and src.startswith("--", i):
            m = LUA_LONG_COMMENT.match(src, i)
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, m.end())
                j = n if j < 0 else j + len(close)
                block = True
            else:
                j = src.find("\n", i)
                j = n if j < 0 else j
                block = False
            text = src[i:j]
            if keep(text):
                out.put(text)
            else:
                out.mark()
                if block:
                    j = after_block(out, src, j)
            i = j
        elif c in "\"'":
            j = min(skip_string(src, i), n)
            out.put(src[i:j])
            i = j
        elif c == "[":
            m = LUA_LONG_STRING.match(src, i)
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, m.end())
                j = n if j < 0 else j + len(close)
                out.put(src[i:j])
                i = j
            else:
                out.put(c)
                i += 1
        else:
            j = i + 1
            while j < n and src[j] not in "-\"'[":
                j += 1
            out.put(src[i:j])
            i = j
    return out.text()


HEREDOC = re.compile(r"<<(-?)[ \t]*\\?(?:(['\"])(.*?)\2|([^\s;&|<>()'\"\\]+))")
SH_SPECIAL = "\\'\"`$()#\n<"
SH_COMMENT_PREV = " \t\n;&|("


def strip_sh(src, keep):
    n = len(src)
    out = Out()
    pending = []

    def skip_braced(i):
        j = i + 2
        depth = 1
        while j < n:
            c = src[j]
            if c == "\\":
                j += 2
                continue
            if c == "'":
                k = src.find("'", j + 1)
                j = n if k < 0 else k + 1
                continue
            if c == '"':
                j = skip_dq_plain(j)
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return n

    def skip_dq_plain(j):
        j += 1
        while j < n:
            if src[j] == "\\":
                j += 2
                continue
            if src[j] == '"':
                return j + 1
            j += 1
        return n

    def skip_backtick(i):
        j = i + 1
        while j < n:
            if src[j] == "\\":
                j += 2
            elif src[j] == "`":
                return j + 1
            else:
                j += 1
        return n

    def try_arith(i):
        """i at '$((' or '(('; return index after matching '))' or -1."""
        p = i + 1 if src[i] == "$" else i
        depth = 0
        j = p
        while j < n:
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return -1

    def consume_heredocs(i):
        nonlocal pending
        for delim, dash in pending:
            while i < n:
                e = src.find("\n", i)
                end = n if e < 0 else e
                line = src[i:end]
                out.put(src[i : end + 1] if e >= 0 else line)
                i = end + 1 if e >= 0 else n
                if (line.lstrip("\t") if dash else line) == delim:
                    break
        pending = []
        return i

    def scan_dq(i):
        out.put('"')
        i += 1
        while i < n:
            c = src[i]
            if c == "\\":
                out.put(src[i : i + 2])
                i += 2
            elif c == '"':
                out.put('"')
                return i + 1
            elif c == "$" and src.startswith("((", i + 1):
                j = try_arith(i)
                if j > 0:
                    out.put(src[i:j])
                    i = j
                else:
                    out.put("$")
                    i += 1
            elif c == "$" and src.startswith("(", i + 1):
                out.put("$(")
                i = scan_code(i + 2, True)
                if i < n:
                    out.put(")")
                    i += 1
            elif c == "$" and src.startswith("{", i + 1):
                j = skip_braced(i)
                out.put(src[i:j])
                i = j
            elif c == "`":
                j = skip_backtick(i)
                out.put(src[i:j])
                i = j
            else:
                j = i + 1
                while j < n and src[j] not in '\\"$`':
                    j += 1
                out.put(src[i:j])
                i = j
        return i

    def scan_code(i, nested):
        depth = 0
        while i < n:
            c = src[i]
            if c == "\\":
                out.put(src[i : i + 2])
                i += 2
            elif c == "'":
                j = src.find("'", i + 1)
                j = n if j < 0 else j + 1
                out.put(src[i:j])
                i = j
            elif c == '"':
                i = scan_dq(i)
            elif c == "`":
                j = skip_backtick(i)
                out.put(src[i:j])
                i = j
            elif c == "$":
                nx = src[i + 1 : i + 2]
                if nx == "(":
                    if src.startswith("((", i + 1):
                        j = try_arith(i)
                        if j > 0:
                            out.put(src[i:j])
                            i = j
                            continue
                    out.put("$(")
                    i = scan_code(i + 2, True)
                    if i < n:
                        out.put(")")
                        i += 1
                elif nx == "{":
                    j = skip_braced(i)
                    out.put(src[i:j])
                    i = j
                elif nx == "'":
                    j = i + 2
                    while j < n and src[j] != "'":
                        j += 2 if src[j] == "\\" else 1
                    j = min(j + 1, n)
                    out.put(src[i:j])
                    i = j
                elif nx == '"':
                    out.put("$")
                    i += 1
                elif nx:
                    out.put(src[i : i + 2])
                    i += 2
                else:
                    out.put("$")
                    i += 1
            elif c == "(":
                if src.startswith("((", i):
                    j = try_arith(i)
                    if j > 0:
                        out.put(src[i:j])
                        i = j
                        continue
                depth += 1
                out.put("(")
                i += 1
            elif c == ")":
                if nested and depth == 0:
                    return i
                if depth:
                    depth -= 1
                out.put(")")
                i += 1
            elif c == "#" and (i == 0 or src[i - 1] in SH_COMMENT_PREV):
                j = src.find("\n", i)
                j = n if j < 0 else j
                text = src[i:j]
                if keep(text):
                    out.put(text)
                else:
                    out.mark()
                i = j
            elif c == "\n":
                out.put("\n")
                i += 1
                if pending:
                    i = consume_heredocs(i)
            elif c == "<" and src.startswith("<<", i):
                if src.startswith("<<<", i):
                    out.put("<<<")
                    i += 3
                else:
                    m = HEREDOC.match(src, i)
                    if m:
                        delim = m.group(3) if m.group(2) else m.group(4)
                        pending.append((delim, m.group(1) == "-"))
                        out.put(m.group(0))
                        i = m.end()
                    else:
                        out.put("<<")
                        i += 2
            else:
                j = i + 1
                while j < n and src[j] not in SH_SPECIAL:
                    j += 1
                out.put(src[i:j])
                i = j
        return i

    scan_code(0, False)
    return out.text()


SCRIPT_TYPE = re.compile(r"""\btype\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
JS_TYPES = {"", "module", "text/babel", "text/jsx", "babel", "jsx"}


def _tag_type(tag):
    m = SCRIPT_TYPE.search(tag)
    if not m:
        return None
    return (m.group(1) or m.group(2) or m.group(3) or "").strip().lower()


def strip_html(src, keep):
    n = len(src)
    out = Out()
    i = 0
    while i < n:
        if src.startswith("<!--", i):
            j = src.find("-->", i + 4)
            j = n if j < 0 else j + 3
            text = src[i:j]
            conditional = text.startswith("<!--[if") or text.startswith("<!--<![endif]")
            if conditional or keep(text):
                out.put(text)
            else:
                out.mark()
                j = after_block(out, src, j)
            i = j
        elif src.startswith("<![CDATA[", i):
            j = src.find("]]>", i)
            j = n if j < 0 else j + 3
            out.put(src[i:j])
            i = j
        elif (
            src[i] == "<" and i + 1 < n and (src[i + 1].isalpha() or src[i + 1] in "!?")
        ):
            j = i + 1
            q = None
            prev = ""
            while j < n:
                ch = src[j]
                if q:
                    if ch == q:
                        q = None
                elif ch in "\"'" and prev == "=":
                    q = ch
                elif ch == ">":
                    j += 1
                    break
                if not ch.isspace():
                    prev = ch
                j += 1
            tag = src[i:j]
            out.put(tag)
            i = j
            m = re.match(r"<([A-Za-z][^\s/>]*)", tag)
            name = m.group(1).lower() if m else ""
            if name in ("script", "style") and not tag.rstrip(">").endswith("/"):
                k = re.compile(r"</" + name + r"\b", re.I).search(src, i)
                end = k.start() if k else n
                body = src[i:end]
                t = _tag_type(tag)
                if name == "script" and (
                    t is None or t in JS_TYPES or "javascript" in t or "ecmascript" in t
                ):
                    body = strip_js(body, keep)
                elif name == "style" and (t is None or t in ("", "text/css")):
                    body = strip_css(body, keep)
                out.put(body)
                i = end
        else:
            j = src.find("<", i + 1)
            j = n if j < 0 else j
            out.put(src[i:j])
            i = j
    return out.text()




def strip_py(src, keep):
    lines = io.StringIO(src).readlines()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            continue
        row, col = tok.start
        text = tok.string
        if row == 1 and text.startswith("#!"):
            continue
        if row <= 2 and re.match(r"#.*?coding[:=]\s*[-\w.]+", text):
            continue
        if keep(text):
            continue
        line = lines[row - 1]
        nl = "\n" if line.endswith("\n") else ""
        lines[row - 1] = line[:col] + M + nl
    return "".join(lines)




def finish(text, squeeze):
    """Drop emptied comment lines, trim trailing blanks, count removals."""
    removed = text.count(M)
    out = []
    dropped = False
    for line in text.split("\n"):
        if M in line:
            line = line.replace(M, "").rstrip()
            if not line.strip():
                dropped = True
                continue
        if (
            squeeze
            and dropped
            and not line.strip()
            and (not out or not out[-1].strip())
        ):
            continue
        if line.strip():
            dropped = False
        out.append(line)
    return "\n".join(out), removed


def strip_text(src, lang, keep, squeeze=False):
    if M in src:
        raise ValueError("file contains the private-use character U+E000")
    head = ""
    if lang in HEAD_LANGS and src.startswith("#!"):
        h, sep, src = src.partition("\n")
        head = h + sep
    funcs = {
        "py": strip_py,
        "js": strip_js,
        "css": strip_css,
        "html": strip_html,
        "json": strip_json,
        "jsonc": strip_json,
        "sh": strip_sh,
        "lua": strip_lua,
        "qml": strip_js,
        "ts": strip_js,
        "c": strip_c,
        "java": strip_java,
        "cs": strip_cs,
        "go": strip_go,
        "kt": strip_kt,
        "swift": strip_swift,
        "rs": strip_rs,
    }
    pat = LANG_KEEP.get(lang)
    if pat:
        base = keep
        keep = lambda t: base(t) or bool(pat.search(t))
    marked = funcs[lang](src, keep)
    body, removed = finish(marked, squeeze)
    return head + body, removed


def detect_lang(path, text):
    ext = os.path.splitext(path)[1].lower()
    if ext in EXT_LANG:
        return EXT_LANG[ext]
    if text.startswith("#!"):
        first = text.split("\n", 1)[0]
        for words, lang in (
            (("python",), "py"),
            (("bash", "/sh", " sh", "zsh", "dash", "ksh"), "sh"),
            (("node", "deno", "bun"), "js"),
            (("qml",), "qml"),
            (("lua",), "lua"),
        ):
            if any(w in first for w in words):
                return lang
    return None


def process_bytes(raw, name, lang_opt, keep, squeeze):
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = (raw[3:] if bom else raw).decode("utf-8")
    crlf = "\r\n" in text
    if crlf:
        text = text.replace("\r\n", "\n")
    lang = lang_opt or detect_lang(name, text)
    if not lang:
        raise ValueError("unknown file type (use --lang)")
    new, removed = strip_text(text, lang, keep, squeeze)
    if lang == "py":
        try:
            before = ast.dump(ast.parse(text))
        except SyntaxError:
            before = None
        if before is not None and before != ast.dump(ast.parse(new)):
            raise ValueError(
                "safety check failed: Python AST changed, file left untouched"
            )
    if crlf:
        new = new.replace("\n", "\r\n")
    data = new.encode("utf-8")
    return (b"\xef\xbb\xbf" + data if bom else data), removed


def write_atomic(path, data):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".strip_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if os.path.exists(path):
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def collect(paths):
    me = os.path.realpath(__file__)
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = sorted(
                    d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
                )
                for nm in sorted(names):
                    fp = os.path.join(root, nm)
                    if (
                        os.path.splitext(nm)[1].lower() in EXT_LANG
                        and os.path.realpath(fp) != me
                    ):
                        files.append((fp, True))
        else:
            files.append((p, False))
    return files


def keep_filter(patterns):
    """Build the `keep` predicate from a list of regex patterns."""
    if not patterns:
        return lambda text: False
    compiled = [re.compile(p) for p in patterns]
    return lambda text: any(r.search(text) for r in compiled)


def _diff_lines(path, raw, new):
    """Unified diff between the on-disk bytes and the stripped bytes."""
    old_text = raw.decode("utf-8-sig", errors="replace").splitlines()
    new_text = new.decode("utf-8-sig", errors="replace").splitlines()
    return list(
        difflib.unified_diff(
            old_text, new_text, fromfile=path, tofile=path, lineterm=""
        )
    )


def strip_files(
    paths,
    lang=None,
    keep=None,
    squeeze=False,
    dry_run=False,
    check=False,
    show_deleted=False,
):
    """Strip comments from `paths`, reporting progress with rich.

    When `show_deleted` is true, a diff of every changed file is printed so the
    removed lines are visible. Returns an exit code: 0 on success, 1 if `check`
    found files that would change, and 2 if any file failed.
    """
    keep_fn = keep_filter(keep)
    files = collect(paths or ["."])
    if not files:
        console.print("[yellow]No matching files found.[/yellow]")
        return 0

    results = []
    diffs = []
    changed = 0
    errors = 0

    with console.status(f"[bold]Scanning {len(files)} file(s)[/bold]", spinner="dots"):
        for path, _ in files:
            try:
                with open(path, "rb") as f:
                    raw = f.read()
                new, removed = process_bytes(raw, path, lang, keep_fn, squeeze)
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                errors += 1
                results.append((path, "error", str(exc)))
                continue

            if new == raw:
                continue

            changed += 1
            if check:
                results.append((path, "would change", f"{removed} comment(s)"))
            elif dry_run:
                results.append((path, "would strip", f"{removed} comment(s)"))
            else:
                try:
                    write_atomic(path, new)
                except OSError as exc:
                    errors += 1
                    results.append((path, "error", str(exc)))
                    continue
                results.append((path, "stripped", f"{removed} comment(s)"))

            if show_deleted:
                diffs.append((path, _diff_lines(path, raw, new)))

    if results:
        table = Table(show_header=True, header_style="bold")
        table.add_column("File", overflow="fold")
        table.add_column("Status", no_wrap=True)
        table.add_column("Comments", overflow="fold")
        for path, status, detail in results:
            style = {
                "stripped": "green",
                "would strip": "yellow",
                "would change": "yellow",
                "error": "red",
            }[status]
            table.add_row(path, f"[{style}]{status}[/{style}]", detail)
        console.print(table)

    for path, lines in diffs:
        if not lines:
            continue
        console.print(f"[bold]{escape(path)}[/bold]")
        console.print(
            Syntax(
                "\n".join(lines),
                "diff",
                theme="ansi_dark",
                background_color="default",
            )
        )

    if errors:
        console.print(
            f"[bold red]{errors} file(s) failed[/bold red]"
            + (f", [green]{changed} file(s) changed[/green]" if changed else "")
        )
        return 2
    if check and changed:
        console.print(f"[bold yellow]{changed} file(s) would change[/bold yellow]")
        return 1
    if changed:
        verb = "would be stripped" if dry_run else "stripped"
        console.print(f"[bold green]{changed} file(s) {verb}[/bold green]")
    else:
        console.print("[green]Nothing to strip.[/green]")
    return 0
