#!/usr/bin/env python3
"""مداني — لغة برمجة عربية بأسلوب Python، تُترجم إلى JavaScript

    python madani.py run   برنامج.mdn          # تشغيل (يحتاج Node.js)
    python madani.py build برنامج.mdn          # ينتج برنامج.mjs
    python madani.py build برنامج.mdn --html   # ينتج صفحة ويب كاملة
"""
import sys
import os
import json
import shutil
import subprocess
import tempfile


class LangError(Exception):
    def __init__(self, msg, line=None):
        super().__init__(msg)
        self.msg = msg
        self.line = line


# ============================================================
# 1) المحلل اللغوي — يدعم المسافات البادئة مثل Python
# ============================================================
KEYWORDS = {
    "اذا", "والا", "طالما", "لكل", "في", "دالة", "صنف", "ارجع", "توقف", "استمر",
    "مرر", "صحيح", "خطأ", "لاشيء", "و", "او", "ليس", "حاول", "استثناء", "اخيرا",
    "ارفع", "انتظر", "غير_متزامنة", "هذا", "الاب", "جديد", "عام",
}
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")
TWO = {"==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "//", "**"}
ONE = set("+-*/%<>=()[]{},:.")
ALIAS = {"،": ","}
CMP = ("==", "!=", "<", ">", "<=", ">=")


def lex(src):
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    toks, i, n, line = [], 0, len(src), 1
    depth, stack, at_start = 0, [0], True

    def add(k, v):
        toks.append((k, v, line))

    while i < n:
        if at_start and depth == 0:
            j, col = i, 0
            while j < n and src[j] in " \t":
                col += 4 if src[j] == "\t" else 1
                j += 1
            if j >= n:
                i = j
                break
            if src[j] == "\n":  # سطر فارغ
                line += 1
                i = j + 1
                continue
            if src[j] == "#":  # سطر تعليق
                while j < n and src[j] != "\n":
                    j += 1
                i = j
                continue
            if col > stack[-1]:
                stack.append(col)
                add("INDENT", "")
            else:
                while col < stack[-1]:
                    stack.pop()
                    add("DEDENT", "")
                if col != stack[-1]:
                    raise LangError("مسافة بادئة غير متسقة", line)
            at_start = False
            i = j
            continue
        c = src[i]
        if c == "\n":
            if depth == 0:
                if toks and toks[-1][0] not in ("NEWLINE", "INDENT", "DEDENT"):
                    add("NEWLINE", "")
                at_start = True
            line += 1
            i += 1
            continue
        if c in " \t":
            i += 1
            continue
        if c == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "\\" and i + 1 < n and src[i + 1] == "\n":  # متابعة السطر
            line += 1
            i += 2
            continue
        if c in "\"'«":
            end = "»" if c == "«" else c
            j, buf, start = i + 1, [], line
            while j < n and src[j] != end:
                if src[j] == "\\" and j + 1 < n:
                    buf.append({"n": "\n", "t": "\t"}.get(src[j + 1], src[j + 1]))
                    j += 2
                else:
                    if src[j] == "\n":
                        line += 1
                    buf.append(src[j])
                    j += 1
            if j >= n:
                raise LangError("نص غير مغلق", start)
            add("str", "".join(buf))
            toks[-1] = ("str", "".join(buf), start)
            i = j + 1
            continue
        if c.isdigit():
            j = i
            while j < n and src[j].isdigit():
                j += 1
            if j + 1 < n and src[j] in ".٫" and src[j + 1].isdigit():
                j += 1
                while j < n and src[j].isdigit():
                    j += 1
            t = src[i:j].translate(DIGITS)
            add("num", float(t) if "." in t else int(t))
            i = j
            continue
        if c.isalpha() or c == "_" or c == "$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            w = src[i:j]
            add("kw" if w in KEYWORDS else "name", w)
            i = j
            continue
        if src[i:i + 2] in TWO:
            add("op", src[i:i + 2])
            i += 2
            continue
        c = ALIAS.get(c, c)
        if c in ONE:
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
                if depth < 0:
                    raise LangError("قوس إغلاق زائد", line)
            add("op", c)
            i += 1
            continue
        raise LangError(f"رمز غير معروف «{c}»", line)
    if depth > 0:
        raise LangError("قوس غير مغلق", line)
    if toks and toks[-1][0] not in ("NEWLINE", "INDENT", "DEDENT"):
        add("NEWLINE", "")
    while len(stack) > 1:
        stack.pop()
        add("DEDENT", "")
    add("EOF", "")
    return toks


# ============================================================
# 2) المحلل النحوي
# ============================================================
EXPECT_NAMES = {"name": "اسم", "str": "نص", "num": "رقم", "INDENT": "سطر مُزاح (مسافة بادئة)",
                "NEWLINE": "نهاية السطر"}


class Parser:
    def __init__(self, toks):
        self.t = toks
        self.p = 0

    def peek(self):
        return self.t[self.p]

    def next(self):
        tok = self.t[self.p]
        if tok[0] != "EOF":
            self.p += 1
        return tok

    def at(self, kind, val=None):
        k, v, _ = self.peek()
        return k == kind and (val is None or v == val)

    def accept(self, kind, val=None):
        return self.next() if self.at(kind, val) else None

    def expect(self, kind, val=None):
        if self.at(kind, val):
            return self.next()
        k, v, line = self.peek()
        want = val or EXPECT_NAMES.get(kind, kind)
        got = {"EOF": "نهاية الملف", "NEWLINE": "نهاية السطر", "INDENT": "مسافة بادئة",
               "DEDENT": "نهاية الكتلة"}.get(k, v)
        raise LangError(f"متوقّع «{want}» لكن وُجد «{got}»", line)

    def skip_nl(self):
        while self.accept("NEWLINE"):
            pass

    def end(self):
        if self.accept("NEWLINE") or self.at("EOF") or self.at("DEDENT"):
            return
        k, v, line = self.peek()
        raise LangError(f"رمز غير متوقع «{v}» — متوقّع نهاية السطر", line)

    # ---- العبارات ----
    def program(self):
        out = []
        self.skip_nl()
        while not self.at("EOF"):
            out.append(self.statement())
            self.skip_nl()
        return out

    def block(self):
        self.expect("op", ":")
        if self.accept("NEWLINE"):
            self.skip_nl()
            self.expect("INDENT")
            out = []
            while not self.at("DEDENT") and not self.at("EOF"):
                out.append(self.statement())
                self.skip_nl()
            self.accept("DEDENT")
            return out
        return [self.statement()]

    def params(self):
        self.expect("op", "(")
        ps = []
        if not self.at("op", ")"):
            while True:
                name = self.expect("name")[1]
                d = self.expr() if self.accept("op", "=") else None
                ps.append((name, d))
                if not self.accept("op", ",") or self.at("op", ")"):
                    break
        self.expect("op", ")")
        return ps

    def funcdef(self, is_async):
        self.expect("kw", "دالة")
        name = self.expect("name")[1]
        ps = self.params()
        return ("def", name, ps, self.block(), is_async)

    def statement(self):
        k, v, line = self.peek()
        if k == "INDENT":
            raise LangError("مسافة بادئة غير متوقعة", line)
        if k == "kw":
            if v == "اذا":
                self.next()
                clauses = [(self.expr(), self.block())]
                other = None
                while self.at("kw", "والا"):
                    self.next()
                    if self.accept("kw", "اذا"):
                        clauses.append((self.expr(), self.block()))
                    else:
                        other = self.block()
                        break
                return ("if", clauses, other)
            if v == "طالما":
                self.next()
                cond = self.expr()
                return ("while", cond, self.block())
            if v == "لكل":
                self.next()
                names = [self.expect("name")[1]]
                while self.accept("op", ","):
                    names.append(self.expect("name")[1])
                self.expect("kw", "في")
                it = self.expr()
                return ("for", names, it, self.block())
            if v == "غير_متزامنة":
                self.next()
                return self.funcdef(True)
            if v == "دالة" and self.t[self.p + 1][0] == "name":
                return self.funcdef(False)
            if v == "صنف":
                self.next()
                name = self.expect("name")[1]
                base = None
                if self.accept("op", "("):
                    base = self.expect("name")[1]
                    self.expect("op", ")")
                return ("class", name, base, self.block())
            if v == "ارجع":
                self.next()
                if self.at("NEWLINE") or self.at("EOF") or self.at("DEDENT"):
                    self.end()
                    return ("return", None)
                e = self.expr()
                self.end()
                return ("return", e)
            if v in ("توقف", "استمر", "مرر"):
                self.next()
                self.end()
                return ({"توقف": "break", "استمر": "continue", "مرر": "pass"}[v],)
            if v == "عام":
                self.next()
                names = [self.expect("name")[1]]
                while self.accept("op", ","):
                    names.append(self.expect("name")[1])
                self.end()
                return ("global", names)
            if v == "حاول":
                self.next()
                body, catch, fin = self.block(), None, None
                if self.accept("kw", "استثناء"):
                    var = self.next()[1] if self.at("name") else None
                    catch = (var, self.block())
                if self.accept("kw", "اخيرا"):
                    fin = self.block()
                if catch is None and fin is None:
                    raise LangError("«حاول» تحتاج إلى «استثناء» أو «اخيرا»", line)
                return ("try", body, catch, fin)
            if v == "ارفع":
                self.next()
                e = self.expr()
                self.end()
                return ("throw", e)
        e = self.expr()
        if self.at("op") and self.peek()[1] in ("=", "+=", "-=", "*=", "/="):
            op = self.next()[1]
            if e[0] not in ("name", "index", "attr"):
                raise LangError("لا يمكن الإسناد إلى هذا التعبير", line)
            val = self.expr()
            self.end()
            return ("assign", e, val, op)
        self.end()
        return ("expr", e)

    # ---- التعابير ----
    def expr(self):
        e = self.logic_or()
        if self.at("kw", "اذا"):  # شرط مختصر:  أ اذا شرط والا ب
            self.next()
            c = self.logic_or()
            self.expect("kw", "والا")
            return ("cond", c, e, self.expr())
        return e

    def logic_or(self):
        l = self.logic_and()
        while self.accept("kw", "او"):
            l = ("or", l, self.logic_and())
        return l

    def logic_and(self):
        l = self.logic_not()
        while self.accept("kw", "و"):
            l = ("and", l, self.logic_not())
        return l

    def logic_not(self):
        if self.accept("kw", "ليس"):
            return ("not", self.logic_not())
        return self.cmp()

    def cmp(self):
        l = self.add()
        while True:
            k, v, line = self.peek()
            if k == "op" and v in CMP:
                self.next()
                l = ("bin", v, l, self.add(), line)
            elif k == "kw" and v == "في":
                self.next()
                l = ("in", l, self.add())
            elif k == "kw" and v == "ليس" and self.t[self.p + 1][:2] == ("kw", "في"):
                self.next()
                self.next()
                l = ("not", ("in", l, self.add()))
            else:
                return l

    def binary(self, ops, sub):
        l = sub()
        while self.peek()[0] == "op" and self.peek()[1] in ops:
            tok = self.next()
            l = ("bin", tok[1], l, sub(), tok[2])
        return l

    def add(self):
        return self.binary(("+", "-"), self.mul)

    def mul(self):
        return self.binary(("*", "/", "//", "%"), self.unary)

    def unary(self):
        if self.accept("op", "-"):
            return ("neg", self.unary())
        if self.accept("kw", "انتظر"):
            return ("await", self.unary())
        return self.power()

    def power(self):
        b = self.postfix()
        if self.accept("op", "**"):
            return ("bin", "**", b, self.unary(), 0)
        return b

    def postfix(self):
        e = self.primary()
        while True:
            if self.accept("op", "("):
                e = ("call", e, self.items(")"))
            elif self.accept("op", "["):
                i = self.expr()
                self.expect("op", "]")
                e = ("index", e, i)
            elif self.accept("op", "."):
                e = ("attr", e, self.expect("name")[1])
            else:
                return e

    def items(self, close):
        args = []
        if not self.at("op", close):
            args.append(self.expr())
            while self.accept("op", ","):
                if self.at("op", close):
                    break
                args.append(self.expr())
        self.expect("op", close)
        return args

    def primary(self):
        k, v, line = self.peek()
        if k in ("num", "str"):
            self.next()
            return ("lit", v)
        if k == "kw":
            if v in ("صحيح", "خطأ", "لاشيء"):
                self.next()
                return ("lit", {"صحيح": True, "خطأ": False, "لاشيء": None}[v])
            if v == "هذا":
                self.next()
                return ("raw", "this")
            if v == "الاب":
                self.next()
                return ("raw", "super")
            if v == "جديد":
                self.next()
                f = ("name", self.expect("name")[1])
                while self.accept("op", "."):
                    f = ("attr", f, self.expect("name")[1])
                args = self.items(")") if self.accept("op", "(") else []
                return ("new", f, args)
            if v in ("دالة", "غير_متزامنة"):
                is_async = self.next()[1] == "غير_متزامنة"
                if is_async:
                    self.expect("kw", "دالة")
                else:
                    pass
                ps = self.params()
                self.expect("op", ":")
                return ("lambda", ps, self.expr(), is_async)
        if k == "name":
            self.next()
            return ("name", v)
        if self.accept("op", "("):
            e = self.expr()
            self.expect("op", ")")
            return e
        if self.accept("op", "["):
            return ("list", self.items("]"))
        if self.accept("op", "{"):
            pairs = []
            while not self.at("op", "}"):
                key = self.expr()
                self.expect("op", ":")
                pairs.append((key, self.expr()))
                if not self.accept("op", ","):
                    break
            self.expect("op", "}")
            return ("dict", pairs)
        got = {"EOF": "نهاية الملف", "NEWLINE": "نهاية السطر", "DEDENT": "نهاية الكتلة"}.get(k, v)
        raise LangError(f"رمز غير متوقع «{got}»", line)


# ============================================================
# 3) مولّد JavaScript
# ============================================================
def sub_bodies(st):
    k = st[0]
    if k == "if":
        return [b for _, b in st[1]] + ([st[2]] if st[2] is not None else [])
    if k == "while":
        return [st[2]]
    if k == "for":
        return [st[3]]
    if k == "try":
        return [st[1]] + ([st[2][1]] if st[2] else []) + ([st[3]] if st[3] else [])
    if k == "def":
        return [st[3]]
    if k == "class":
        return [st[3]]
    return []


def walk(stmts):
    for st in stmts:
        yield st
        for b in sub_bodies(st):
            yield from walk(b)


def collect(stmts, names, globs, declared):
    """المتغيرات المُسندة في نطاق الدالة الحالية (لرفعها بـ let)"""
    for st in stmts:
        k = st[0]
        if k == "assign" and st[1][0] == "name":
            names[st[1][1]] = True
        elif k == "global":
            globs.update(st[1])
        elif k in ("def", "class"):
            declared.add(st[1])
        else:
            for b in sub_bodies(st):
                collect(b, names, globs, declared)


class Gen:
    def __init__(self, classes):
        self.classes = classes

    def args(self, xs):
        return ", ".join(self.expr(x) for x in xs)

    def expr(self, e):
        k = e[0]
        if k == "lit":
            v = e[1]
            if v is True:
                return "true"
            if v is False:
                return "false"
            if v is None:
                return "null"
            if isinstance(v, str):
                return json.dumps(v, ensure_ascii=False)
            return repr(v)
        if k in ("name", "raw"):
            return e[1]
        if k == "list":
            return "[" + self.args(e[1]) + "]"
        if k == "dict":
            parts = []
            for key, val in e[1]:
                if key[0] == "lit" and isinstance(key[1], str):
                    ks = self.expr(key)
                else:
                    ks = "[" + self.expr(key) + "]"
                parts.append(f"{ks}: {self.expr(val)}")
            return "{" + ", ".join(parts) + "}"
        if k == "neg":
            return f"(-{self.expr(e[1])})"
        if k == "not":
            return f"(!{self.expr(e[1])})"
        if k == "and":
            return f"({self.expr(e[1])} && {self.expr(e[2])})"
        if k == "or":
            return f"({self.expr(e[1])} || {self.expr(e[2])})"
        if k == "bin":
            op, a, b = e[1], self.expr(e[2]), self.expr(e[3])
            if op == "//":
                return f"Math.floor({a} / {b})"
            op = {"==": "===", "!=": "!=="}.get(op, op)
            return f"({a} {op} {b})"
        if k == "in":
            return f"_في({self.expr(e[1])}, {self.expr(e[2])})"
        if k == "cond":
            return f"({self.expr(e[1])} ? {self.expr(e[2])} : {self.expr(e[3])})"
        if k == "index":
            o, i = self.expr(e[1]), e[2]
            if e[1][0] == "lit":
                o = f"({o})"
            if i[0] == "neg":
                return f"{o}.at({self.expr(i)})"
            return f"{o}[{self.expr(i)}]"
        if k == "attr":
            o = self.expr(e[1])
            if e[1][0] == "lit":
                o = f"({o})"
            return f"{o}.{e[2]}"
        if k == "call":
            f = e[1]
            if f[0] == "name" and f[1] in self.classes:
                return f"new {f[1]}({self.args(e[2])})"
            return f"{self.expr(f)}({self.args(e[2])})"
        if k == "new":
            return f"new {self.expr(e[1])}({self.args(e[2])})"
        if k == "lambda":
            ps = self.params(e[1])
            return f"({'async ' if e[3] else ''}({ps}) => ({self.expr(e[2])}))"
        if k == "await":
            return f"(await {self.expr(e[1])})"
        raise LangError("تعبير غير مدعوم")

    def params(self, ps):
        return ", ".join(n if d is None else f"{n} = {self.expr(d)}" for n, d in ps)

    # ---- العبارات ----
    def blk(self, stmts, ind):
        return "".join(self.stmt(s, ind) for s in stmts)

    def body(self, stmts, ind, exclude=()):
        names, globs, declared = {}, set(), set()
        collect(stmts, names, globs, declared)
        hoist = [n for n in names if n not in exclude and n not in globs and n not in declared]
        out = ("  " * ind + "let " + ", ".join(hoist) + ";\n") if hoist else ""
        return out + self.blk(stmts, ind)

    def fn(self, name, params, body, is_async, ind, method):
        p = "  " * ind
        pre = "async " if is_async else ""
        ps = self.params(params)
        if method:
            nm = {"بناء": "constructor", "نصي": "toString"}.get(name, name)
            head = f"{pre}{nm}({ps}) {{"
        else:
            head = f"{pre}function {name}({ps}) {{"
        inner = self.body(body, ind + 1, {n for n, _ in params})
        return f"{p}{head}\n{inner}{p}}}\n"

    def stmt(self, st, ind):
        p = "  " * ind
        k = st[0]
        if k == "expr":
            return f"{p}{self.expr(st[1])};\n"
        if k == "assign":
            _, t, v, op = st
            if t[0] == "index":
                target = f"{self.expr(t[1])}[{self.expr(t[2])}]"
            else:
                target = self.expr(t)
            return f"{p}{target} {op} {self.expr(v)};\n"
        if k == "if":
            out = ""
            for i, (c, b) in enumerate(st[1]):
                kw = "if" if i == 0 else "} else if"
                out += f"{p}{kw} ({self.expr(c)}) {{\n" + self.blk(b, ind + 1)
            if st[2] is not None:
                out += f"{p}}} else {{\n" + self.blk(st[2], ind + 1)
            return out + f"{p}}}\n"
        if k == "while":
            return f"{p}while ({self.expr(st[1])}) {{\n{self.blk(st[2], ind + 1)}{p}}}\n"
        if k == "for":
            names = st[1]
            tgt = names[0] if len(names) == 1 else "[" + ", ".join(names) + "]"
            return (f"{p}for (const {tgt} of _تكرار({self.expr(st[2])})) {{\n"
                    f"{self.blk(st[3], ind + 1)}{p}}}\n")
        if k == "def":
            return self.fn(st[1], st[2], st[3], st[4], ind, False)
        if k == "class":
            _, name, base, body = st
            ext = f" extends {base}" if base else ""
            out = f"{p}class {name}{ext} {{\n"
            for m in body:
                if m[0] == "def":
                    out += self.fn(m[1], m[2], m[3], m[4], ind + 1, True)
                elif m[0] != "pass":
                    raise LangError(f"داخل الصنف «{name}» يُسمح بتعريف الدوال فقط")
            return out + f"{p}}}\n"
        if k == "return":
            return f"{p}return{'' if st[1] is None else ' ' + self.expr(st[1])};\n"
        if k == "break":
            return f"{p}break;\n"
        if k == "continue":
            return f"{p}continue;\n"
        if k in ("pass", "global"):
            return ""
        if k == "throw":
            return f"{p}throw {self.expr(st[1])};\n"
        if k == "try":
            out = f"{p}try {{\n{self.blk(st[1], ind + 1)}"
            if st[2]:
                var = st[2][0] or "_خطأ"
                out += f"{p}}} catch ({var}) {{\n{self.blk(st[2][1], ind + 1)}"
            if st[3]:
                out += f"{p}}} finally {{\n{self.blk(st[3], ind + 1)}"
            return out + f"{p}}}\n"
        raise LangError("عبارة غير مدعومة")


# ============================================================
# 4) بيئة التشغيل (تُضاف أعلى كل برنامج): دوال عربية للويب والخادم
# ============================================================
PRELUDE = r'''// ===== بيئة التشغيل =====
const _عرف = (o, n, f) => Object.defineProperty(o, n, { value: f, writable: true, configurable: true, enumerable: false });
const _خاصية = (o, n, g, s) => Object.defineProperty(o, n, { get: g, set: s, configurable: true, enumerable: false });
const _نصي = (v, d = false) => {
  if (v === true) return "صحيح";
  if (v === false) return "خطأ";
  if (v == null) return "لاشيء";
  if (typeof v === "string") return d ? JSON.stringify(v) : v;
  if (Array.isArray(v)) return "[" + v.map((x) => _نصي(x, true)).join("، ") + "]";
  if (typeof v === "function") return "<دالة " + (v.name || "") + ">";
  if (typeof v === "object" && Object.getPrototypeOf(v) === Object.prototype)
    return "{" + Object.entries(v).map(([k, x]) => JSON.stringify(k) + ": " + _نصي(x, true)).join("، ") + "}";
  return String(v);
};
function _في(a, b) {
  if (typeof b === "string" || Array.isArray(b)) return b.includes(a);
  if (b && typeof b === "object") return a in b;
  return false;
}
function _تكرار(x) {
  if (x == null) throw new TypeError("لا يمكن التكرار على قيمة فارغة");
  if (typeof x[Symbol.iterator] === "function") return x;
  if (typeof x === "object") return Object.keys(x);
  throw new TypeError("لا يمكن التكرار على هذا النوع");
}

// --- دوال أساسية ---
const اطبع = (...a) => console.log(...a.map((x) => _نصي(x)));
const نص = (x) => _نصي(x);
function طول(x) {
  if (x != null && x.length !== undefined) return x.length;
  if (x && typeof x === "object") return Object.keys(x).length;
  throw new TypeError("لا يمكن حساب طول هذا النوع");
}
function مدى(a, b, c) {
  let s = 0, e = a, st = 1;
  if (b !== undefined) { s = a; e = b; if (c !== undefined) st = c; }
  if (st === 0) throw new RangeError("الخطوة لا يمكن أن تكون صفراً");
  const r = [];
  for (let i = s; st > 0 ? i < e : i > e; i += st) r.push(i);
  return r;
}
function عدد(x) {
  if (typeof x === "string") x = x.replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d)).replace("٫", ".").trim();
  const n = (x === "" || x === null) ? NaN : Number(x);
  if (Number.isNaN(n)) throw new TypeError("لا يمكن تحويل القيمة إلى عدد");
  return n;
}
const جذر = Math.sqrt, ارضية = Math.floor, سقف = Math.ceil, تقريب = Math.round, مطلق = Math.abs;
const اكبر = Math.max, اصغر = Math.min, عشوائي = Math.random, قوة = Math.pow;
const مفاتيح = (o) => Object.keys(o), قيم = (o) => Object.values(o), عناصر = (o) => Object.entries(o);
const جيسون = { حول: (x) => JSON.stringify(x), حلل: (s) => JSON.parse(s) };
const تاريخ = Date, الخطأ = Error, الان = () => Date.now();
const بعد = (ms, f) => setTimeout(f, ms);
const كرر_كل = (ms, f) => setInterval(f, ms);
const انتظار = (ms) => new Promise((r) => setTimeout(r, ms));
async function اقرأ(q = "") {
  if (typeof window !== "undefined" && typeof prompt === "function") return prompt(q);
  const rl = (await import("node:readline/promises")).createInterface({ input: process.stdin, output: process.stdout });
  const a = await rl.question(q);
  rl.close();
  return a;
}
async function جلب(url, خيارات) {
  const r = await fetch(url, خيارات);
  if (!r.ok) throw new Error("فشل الطلب: " + r.status);
  const t = await r.text();
  try { return JSON.parse(t); } catch (_) { return t; }
}

// --- أسماء عربية لدوال القوائم والنصوص ---
const _بدائل = (proto, m) => {
  for (const [ar, en] of Object.entries(m)) _عرف(proto, ar, function (...a) { return this[en](...a); });
};
_بدائل(Array.prototype, { اضف: "push", احذف_اخر: "pop", احذف_اول: "shift", ادرج_اولا: "unshift", اعكس: "reverse",
  اربط: "join", شريحة: "slice", يحتوي: "includes", خريطة: "map", رشح: "filter", لكل_عنصر: "forEach",
  ابحث: "find", فهرس: "indexOf" });
_عرف(Array.prototype, "رتب", function (f) { return this.sort(f || ((a, b) => (a < b ? -1 : a > b ? 1 : 0))); });
_بدائل(String.prototype, { كبير: "toUpperCase", صغير: "toLowerCase", قسم: "split", استبدل: "replaceAll",
  يحتوي: "includes", يبدأ_ب: "startsWith", ينتهي_ب: "endsWith", نظف: "trim", شريحة: "slice", فهرس: "indexOf" });
_خاصية(Error.prototype, "رسالة", function () { return this.message; });

// --- الويب ---
const _متصفح = (f) => (typeof document !== "undefined" ? f : () => { throw new Error("هذه الدالة تعمل في المتصفح فقط"); });
const عنصر = _متصفح((s) => document.querySelector(s));
const كل_العناصر = _متصفح((s) => Array.from(document.querySelectorAll(s)));
const انشئ_عنصر = _متصفح((tag, t = "") => { const e = document.createElement(tag); e.textContent = t; return e; });
const ارسم = _متصفح((html) => { (document.querySelector("#التطبيق") || document.body).innerHTML = html; });
const _احداث = { نقر: "click", تغيير: "input", ارسال: "submit", مفتاح: "keydown", تحميل: "load", مرور: "mouseover" };
if (typeof Element !== "undefined") {
  _خاصية(Element.prototype, "نص", function () { return this.textContent; }, function (v) { this.textContent = v; });
  _خاصية(Element.prototype, "محتوى", function () { return this.innerHTML; }, function (v) { this.innerHTML = v; });
  _خاصية(Element.prototype, "قيمة", function () { return this.value; }, function (v) { this.value = v; });
  _عرف(Element.prototype, "عند", function (h, f) { this.addEventListener(_احداث[h] || h, f); return this; });
  _عرف(Element.prototype, "اضف_ابن", function (c) { this.appendChild(typeof c === "string" ? document.createTextNode(c) : c); return this; });
  _عرف(Element.prototype, "اخف", function () { this.style.display = "none"; return this; });
  _عرف(Element.prototype, "اظهر", function () { this.style.display = ""; return this; });
}

// --- الخادم (Node.js) ---
async function خادم(port, handler) {
  if (typeof process === "undefined") throw new Error("هذه الدالة تعمل على Node.js فقط");
  const http = await import("node:http");
  http.createServer(async (req, res) => {
    try {
      const chunks = [];
      for await (const c of req) chunks.push(c);
      const r = await handler({ مسار: req.url, طريقة: req.method, جسم: Buffer.concat(chunks).toString("utf8"), ترويسات: req.headers });
      if (r !== null && typeof r === "object") {
        res.setHeader("content-type", "application/json; charset=utf-8");
        res.end(JSON.stringify(r));
      } else {
        res.setHeader("content-type", "text/html; charset=utf-8");
        res.end(r == null ? "" : String(r));
      }
    } catch (e) {
      res.statusCode = 500;
      res.end("خطأ في الخادم: " + e.message);
    }
  }).listen(port);
  console.log("الخادم يعمل على http://localhost:" + port);
}
// ===== نهاية بيئة التشغيل =====
'''

HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%TITLE%%</title>
<style>
  body { font-family: system-ui, "Segoe UI", Tahoma, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; line-height: 1.7; }
  button, input { font: inherit; padding: .4rem .8rem; }
</style>
</head>
<body>
<div id="التطبيق"></div>
<script type="module">
%%JS%%
</script>
</body>
</html>
'''


def compile_source(src):
    tree = Parser(lex(src)).program()
    classes = {st[1] for st in walk(tree) if st[0] == "class"}
    code = Gen(classes).body(tree, 1)
    return "// مُولَّد من لغة مداني\n" + PRELUDE + "\n{\n" + code + "}\n"


def build(path, html=False):
    with open(path, encoding="utf-8") as f:
        js = compile_source(f.read())
    base = os.path.splitext(path)[0]
    if html:
        out = base + ".html"
        text = (HTML_TEMPLATE.replace("%%TITLE%%", os.path.basename(base))
                .replace("%%JS%%", js.replace("</script", "<\\/script")))
    else:
        out, text = base + ".mjs", js
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    return out


def run_file(path):
    node = shutil.which("node")
    if not node:
        print("لم يُعثر على Node.js — ثبّته من https://nodejs.org", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        js = compile_source(f.read())
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "main.mjs")
        with open(out, "w", encoding="utf-8") as f:
            f.write(js)
        sys.exit(subprocess.call([node, out]))


USAGE = """الاستخدام:
  python madani.py run   برنامج.mdn
  python madani.py build برنامج.mdn [--html]"""


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    a = sys.argv[1:]
    if len(a) < 2 or a[0] not in ("run", "build"):
        print(USAGE)
        return
    try:
        if a[0] == "build":
            print("تم إنشاء:", build(a[1], "--html" in a))
        else:
            run_file(a[1])
    except LangError as e:
        where = f" في السطر {e.line}" if e.line else ""
        print(f"خطأ{where}: {e.msg}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("الملف غير موجود", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
