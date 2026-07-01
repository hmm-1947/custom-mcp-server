from pathlib import Path

from .languages import get_parser


def parse_file(path: str):
    parser = get_parser(path)

    source = Path(path).read_bytes()

    tree = parser.parse(source)

    return tree, source