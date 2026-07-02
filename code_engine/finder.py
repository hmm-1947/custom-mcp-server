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
)


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



def list_functions(path: str):
    """Return all functions in a source file with name, signature, and line."""

    tree, source = parse_file(path)

    functions = []

    for node in walk(tree.root_node):

        if node.type in (
            "function_definition",
            "method_definition",
            "method_declaration",
            "function_declaration",
        ):

            for child in node.children:

                if child.type == "identifier":

                    functions.append({
                        "name": source[child.start_byte:child.end_byte].decode("utf-8"),
                        "signature": _extract_signature(node, source),
                        "line": node.start_point[0] + 1,
                    })

                    break

        elif node.type == "decorated_definition":

            for child in node.children:

                if child.type == "function_definition":

                    for identifier in child.children:

                        if identifier.type == "identifier":

                            functions.append({
                                "name": source[
                                    identifier.start_byte:
                                    identifier.end_byte
                                ].decode("utf-8"),
                                "signature": _extract_signature(child, source),
                                "line": node.start_point[0] + 1,
                            })

                            break

    return functions

def find_function(path: str, function_name: str):
    """Return the Tree-sitter node and source bytes for a function."""

    tree, source = parse_file(path)

    for node in walk(tree.root_node):

        if node.type in (
            "function_definition",
            "method_definition",
            "method_declaration",
            "function_declaration",
        ):

            for child in node.children:

                if child.type == "identifier":

                    name = source[
                        child.start_byte:
                        child.end_byte
                    ].decode("utf-8")

                    if name == function_name:
                        return node, source

        elif node.type == "decorated_definition":

            for child in node.children:

                if child.type == "function_definition":

                    for identifier in child.children:

                        if identifier.type == "identifier":

                            name = source[
                                identifier.start_byte:
                                identifier.end_byte
                            ].decode("utf-8")

                            if name == function_name:
                                return child, source
    

    return None, None


def find_class(path: str, class_name: str):
    """Return the Tree-sitter node and source bytes for a class."""

    tree, source = parse_file(path)

    for node in walk(tree.root_node):

        if node.type in (
            "class_definition",
            "class_declaration",
        ):

            for child in node.children:

                if child.type == "identifier":

                    name = source[
                        child.start_byte:
                        child.end_byte
                    ].decode("utf-8")

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
        node.end_byte
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
        node.end_byte
    ].decode("utf-8")



def list_classes(path: str):
    """Return all classes in a source file with name, signature, and line."""

    tree, source = parse_file(path)

    classes = []

    for node in walk(tree.root_node):

        if node.type in (
            "class_definition",
            "class_declaration",
        ):

            for child in node.children:

                if child.type == "identifier":

                    classes.append({
                        "name": source[
                            child.start_byte:
                            child.end_byte
                        ].decode("utf-8"),
                        "signature": _extract_signature(node, source),
                        "line": node.start_point[0] + 1,
                    })

                    break

    return classes



def find_symbols_in_file(path: str, query: str):
    """Return functions/classes in a file whose name contains query (case-insensitive)."""

    tree, source = parse_file(path)

    query_lower = query.lower()
    matches = []

    FUNC_TYPES = (
        "function_definition",
        "method_definition",
        "method_declaration",
        "function_declaration",
    )
    CLASS_TYPES = (
        "class_definition",
        "class_declaration",
    )

    def add_match(identifier_node, kind):
        name = source[
            identifier_node.start_byte:
            identifier_node.end_byte
        ].decode("utf-8")

        if query_lower in name.lower():
            matches.append({
                "name": name,
                "type": kind,
                "line": identifier_node.start_point[0] + 1,
            })

    for node in walk(tree.root_node):

        if node.type in FUNC_TYPES:
            for child in node.children:
                if child.type == "identifier":
                    add_match(child, "function")
                    break

        elif node.type in CLASS_TYPES:
            for child in node.children:
                if child.type == "identifier":
                    add_match(child, "class")
                    break

        elif node.type == "decorated_definition":
            for child in node.children:
                if child.type == "function_definition":
                    for identifier in child.children:
                        if identifier.type == "identifier":
                            add_match(identifier, "function")
                            break

    return matches