from pathlib import Path

from .finder import find_function, _body_end_byte


def replace_function(
    path: str,
    function_name: str,
    new_function: str,
):
    node, source = find_function(path, function_name)

    if node is None:
        raise ValueError(f"Function '{function_name}' not found")

    end_byte = _body_end_byte(node)

    new_source = (
        source[:node.start_byte]
        + new_function.encode("utf-8")
        + source[end_byte:]
    )

    Path(path).write_bytes(new_source)

    return f"Replaced function '{function_name}'"