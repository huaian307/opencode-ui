# -*- coding: utf-8 -*-
"""JS bracket balance checker.

Skips strings, template literals (with nested ${}), comments and regex literals.
Usage: python jsbalance.py <file.js>
"""

import re
import sys

# 这些关键字后面出现的 / 才是正则字面量；其余情况（标识符/数字/右括号之后）是除号
REGEX_KEYWORDS = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "do", "else", "case", "throw", "yield", "await",
}


def check(path):
    src = open(path, encoding="utf-8", errors="replace").read()
    i, n = 0, len(src)
    line = 1
    depth = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    tmpl = []
    prev = ""

    def err(msg, ln):
        print("  [BAD] line %d: %s" % (ln, msg))
        return False

    ok = True
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                if src[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue
        if c in "'\"":
            q = c
            i += 1
            while i < n and src[i] != q:
                if src[i] == "\\":
                    i += 1
                elif src[i] == "\n":
                    line += 1
                i += 1
            i += 1
            prev = "x"
            continue
        if c == "`":
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "`":
                    i += 1
                    break
                if src[i] == "\n":
                    line += 1
                    i += 1
                    continue
                if src[i] == "$" and i + 1 < n and src[i + 1] == "{":
                    tmpl.append((len(stack), line))
                    depth["{"] += 1
                    stack.append("{")
                    i += 2
                    break
                i += 1
            prev = "x"
            continue
        if c == "/":
            # 正则 vs 除号：标识符/数字/右括号之后的 / 是除号（否则 window.a / 2 会被当成正则，吞掉后面的括号）
            allow_regex = True
            if prev in ")]}":
                allow_regex = False
            elif prev and (prev.isalnum() or prev in "_$"):
                m = re.search(r"([A-Za-z_$][\w$]*)\s*$", src[:i])
                allow_regex = bool(m) and m.group(1) in REGEX_KEYWORDS
            if allow_regex:
                j = i + 1
                cls = False
                while j < n:
                    ch = src[j]
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "[":
                        cls = True
                    elif ch == "]":
                        cls = False
                    elif ch == "/" and not cls:
                        break
                    elif ch == "\n":
                        break
                    j += 1
                if j < n and src[j] == "/":
                    j += 1
                    while j < n and src[j].isalpha():
                        j += 1
                    i = j
                    prev = "x"
                    continue
        if c in "([{":
            depth[c] += 1
            stack.append(c)
            prev = c
            i += 1
            continue
        if c in ")]}":
            want = pairs[c]
            if not stack or stack[-1] != want:
                ok = err("stray %s (top=%s)" % (c, stack[-1] if stack else "empty"), line)
                i += 1
                continue
            stack.pop()
            depth[want] -= 1
            if c == "}" and tmpl and tmpl[-1][0] == len(stack):
                tmpl.pop()
                i += 1
                while i < n:
                    if src[i] == "\\":
                        i += 2
                        continue
                    if src[i] == "`":
                        i += 1
                        break
                    if src[i] == "\n":
                        line += 1
                        i += 1
                        continue
                    if src[i] == "$" and i + 1 < n and src[i + 1] == "{":
                        tmpl.append((len(stack), line))
                        depth["{"] += 1
                        stack.append("{")
                        i += 2
                        break
                    i += 1
                prev = "x"
                continue
            prev = c
            i += 1
            continue
        if not c.isspace():
            prev = c
        i += 1

    print("  file : %s" % path)
    print("  lines: %d" % line)
    for k in "([{":
        print("  %s depth: %d %s" % (k, depth[k], "OK" if depth[k] == 0 else "BAD"))
        ok = ok and depth[k] == 0
    if stack:
        print("  [BAD] unclosed: %s" % "".join(stack[:40]))
        ok = False
    if tmpl:
        print("  [BAD] unclosed template interpolation at lines: %s" % [t[1] for t in tmpl])
        ok = False
    print("  result: %s" % ("PASS" if ok else "FAIL"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if all(check(p) for p in sys.argv[1:]) else 1)
