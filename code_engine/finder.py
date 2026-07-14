from tree_sitter import Node

from .parser import parse_file


def walk(node: Node):
    """Yield every node in the syntax tree."""

    yield node

    for child in node.children:
        yield from walk(child)

def list_nodes(path: str):
    """Return every node type in the file."""

    tree, _ = parse_file(path)

    nodes = []

    for node in walk(tree.root_node):
        nodes.append(node.type)

    return nodes


BODY_TYPES = (
    "block",
    "statement_block",
    "class_body",
    "function_body",
)

# Node types that represent a function/method across the supported
# languages. Different grammars name these differently - e.g. Python/Java/JS
# use "function_definition"/"method_declaration", while the Dart grammar
# (derived from the Dart language spec rather than a Java/JS copy) uses
# "function_signature"/"method_signature"/etc, and its body is a *sibling*
# node rather than a child. Both shapes are handled by _body_end_byte below.
FUNC_TYPES = (
    "function_definition",
    "method_definition",
    "method_declaration",
    "function_declaration",
    "generator_function_declaration",
    "function_signature",
    "method_signature",
    "getter_signature",
    "setter_signature",
    "constructor_signature",
    "factory_constructor_signature",
    "operator_signature",
)

CLASS_TYPES = (
    "class_definition",
    "class_declaration",
    "mixin_declaration",
    "extension_declaration",
    "interface_declaration",
)


def _get_name_node(node: Node):
    """Return the identifier node for a function/class/method declaration.

    Tries the grammar's "name" field first (works across virtually every
    tree-sitter grammar, including Dart's), then falls back to scanning
    direct children for something identifier-shaped. This avoids having to
    hardcode a different lookup strategy per language.
    """

    name = node.child_by_field_name("name")
    if name is not None:
        return name

    for child in node.children:
        if child.type in ("identifier", "type_identifier", "property_identifier"):
            return child

    return None


def _body_end_byte(node: Node) -> int:
    """Return the end_byte that fully covers a declaration's body.

    Most grammars nest the body inside the declaration node itself, so
    node.end_byte is already correct. Some grammars (e.g. Dart's, where a
    top-level/method "signature" node is a distinct sibling from its
    "function_body") split signature and body into siblings at the same
    level - in that case we extend the span forward to include the body
    (or the terminating ';' for abstract/external members without a body).

    The matching body/';' is not always the *immediate* next sibling -
    there can be intervening tokens (comments, whitespace-only nodes)
    depending on the grammar - so we scan forward until we hit one, but
    stop if we hit another declaration first to avoid swallowing
    unrelated code.
    """

    for child in node.children:
        if child.type in BODY_TYPES:
            return node.end_byte

    sib = node.next_sibling

    while sib is not None:
        if sib.type in BODY_TYPES or sib.type == ";":
            return sib.end_byte

        if sib.type in FUNC_TYPES or sib.type in CLASS_TYPES or sib.type == "decorated_definition":
            break

        sib = sib.next_sibling

    return node.end_byte


def _extract_signature(node: Node, source: bytes) -> str:
    """Return the source text from the node's start up to (not including) its body."""

    body = None

    for child in node.children:
        if child.type in BODY_TYPES:
            body = child
            break

    end = body.start_byte if body is not None else node.end_byte

    text = source[node.start_byte:end].decode("utf-8").strip()

    return text.rstrip("{").rstrip(":").strip()



def _iter_definitions(tree_root):
    """Yield (node, name_node, kind) for every function/class-like definition.

    Handles Python's "decorated_definition" wrapper (decorators sit outside
    the function_definition node) as well as plain top-level/method nodes
    across all supported languages.
    """

    for node in walk(tree_root):

        if node.type in FUNC_TYPES:
            name_node = _get_name_node(node)
            if name_node is not None:
                yield node, name_node, "function"

        elif node.type in CLASS_TYPES:
            name_node = _get_name_node(node)
            if name_node is not None:
                yield node, name_node, "class"

        elif node.type == "decorated_definition":
            for child in node.children:
                if child.type in FUNC_TYPES or child.type in CLASS_TYPES:
                    name_node = _get_name_node(child)
                    if name_node is not None:
                        kind = "function" if child.type in FUNC_TYPES else "class"
                        yield child, name_node, kind
                    break


def list_functions(path: str):
    """Return all functions in a source file with name, signature, and line."""

    tree, source = parse_file(path)

    functions = []

    for node, name_node, kind in _iter_definitions(tree.root_node):
        if kind != "function":
            continue

        functions.append({
            "name": source[name_node.start_byte:name_node.end_byte].decode("utf-8"),
            "signature": _extract_signature(node, source),
            "line": node.start_point[0] + 1,
        })

    return functions


def find_function(path: str, function_name: str):
    """Return the Tree-sitter node and source bytes for a function."""

    tree, source = parse_file(path)

    for node, name_node, kind in _iter_definitions(tree.root_node):
        if kind != "function":
            continue

        name = source[name_node.start_byte:name_node.end_byte].decode("utf-8")

        if name == function_name:
            return node, source

    return None, None


def find_class(path: str, class_name: str):
    """Return the Tree-sitter node and source bytes for a class."""

    tree, source = parse_file(path)

    for node, name_node, kind in _iter_definitions(tree.root_node):
        if kind != "class":
            continue

        name = source[name_node.start_byte:name_node.end_byte].decode("utf-8")

        if name == class_name:
            return node, source

    return None, None


def read_function(
    path: str,
    function_name: str,
):
    """Return the source code of a function."""

    node, source = find_function(path, function_name)

    if node is None:
        raise ValueError(f"Function '{function_name}' not found")

    return source[
        node.start_byte:
        _body_end_byte(node)
    ].decode("utf-8")


def read_class(
    path: str,
    class_name: str,
):
    """Return the source code of a class."""

    node, source = find_class(path, class_name)

    if node is None:
        raise ValueError(f"Class '{class_name}' not found")

    return source[
        node.start_byte:
        _body_end_byte(node)
    ].decode("utf-8")



def list_classes(path: str):
    """Return all classes in a source file with name, signature, and line."""

    tree, source = parse_file(path)

    classes = []

    for node, name_node, kind in _iter_definitions(tree.root_node):
        if kind != "class":
            continue

        classes.append({
            "name": source[name_node.start_byte:name_node.end_byte].decode("utf-8"),
            "signature": _extract_signature(node, source),
            "line": node.start_point[0] + 1,
        })

    return classes



def find_symbols_in_file(path: str, query: str):
    """Return functions/classes in a file whose name contains query (case-insensitive)."""

    tree, source = parse_file(path)

    query_lower = query.lower()
    matches = []

    for node, name_node, kind in _iter_definitions(tree.root_node):
        name = source[name_node.start_byte:name_node.end_byte].decode("utf-8")

        if query_lower in name.lower():
            end_byte = _body_end_byte(node)
            end_line = source[:end_byte].count(b"\n") + 1

            matches.append({
                "name": name,
                "type": kind,
                "line": name_node.start_point[0] + 1,
                "end_line": end_line,
            })

    return matches