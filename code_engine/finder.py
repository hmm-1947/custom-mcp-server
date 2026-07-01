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

def list_functions(path: str):
    """Return all function names in a source file."""

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

                    functions.append(
                        source[child.start_byte:child.end_byte].decode("utf-8")
                    )

                    break

        elif node.type == "decorated_definition":

            for child in node.children:

                if child.type == "function_definition":

                    for identifier in child.children:

                        if identifier.type == "identifier":

                            functions.append(
                                source[
                                    identifier.start_byte:
                                    identifier.end_byte
                                ].decode("utf-8")
                            )

                            break

    return functions

def find_function(path: str, function_name: str):
    """Return the Tree-sitter node for a function."""

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
                        return node

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
                                return child

    return None