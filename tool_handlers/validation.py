from pathlib import Path


STRUCTURAL_EXTENSIONS = frozenset({
    ".c", ".cc", ".cpp", ".cxx", ".dart", ".h", ".hpp", ".java",
    ".js", ".jsx", ".json", ".kt", ".swift", ".ts", ".tsx",
})
OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
CLOSING = frozenset(OPEN_TO_CLOSE.values())


def validate_structure(path: str | Path, content: str) -> dict | None:
    """Check bracket balance outside string literals and comments.

    Returns None when validation is not applicable or succeeds; otherwise a
    small error payload suitable for returning directly from the edit tool.
    """
    if Path(path).suffix.lower() not in STRUCTURAL_EXTENSIONS:
        return None

    stack: list[tuple[str, int]] = []
    line = 1
    index = 0
    quote: str | None = None
    line_comment = False
    block_comment = False

    while index < len(content):
        char = content[index]
        next_char = content[index + 1] if index + 1 < len(content) else ""

        if line_comment:
            if char == "\n":
                line += 1
                line_comment = False
            index += 1
            continue

        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                continue
            if char == "\n":
                line += 1
            index += 1
            continue

        if quote is not None:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            elif char == "\n":
                line += 1
            index += 1
            continue

        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if char in OPEN_TO_CLOSE:
            stack.append((char, line))
        elif char in CLOSING:
            if not stack:
                return {
                    "message": "Structural validation failed",
                    "line": line,
                    "expected": None,
                    "found": char,
                }
            opening, opening_line = stack.pop()
            expected = OPEN_TO_CLOSE[opening]
            if char != expected:
                return {
                    "message": "Structural validation failed",
                    "line": line,
                    "expected": expected,
                    "found": char,
                    "opened_at_line": opening_line,
                }
        if char == "\n":
            line += 1
        index += 1

    if stack:
        opening, opening_line = stack[-1]
        return {
            "message": "Structural validation failed",
            "line": opening_line,
            "expected": OPEN_TO_CLOSE[opening],
            "found": "end of file",
        }
    return None
