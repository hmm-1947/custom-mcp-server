from pathlib import Path

from .languages import get_parser

_CACHE: dict[str, tuple[float, object, bytes]] = {}


def parse_file(path: str):
    p = Path(path)
    mtime = p.stat().st_mtime

    cached = _CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1], cached[2]

    parser = get_parser(path)
    source = p.read_bytes()
    tree = parser.parse(source)

    _CACHE[path] = (mtime, tree, source)

    return tree, source