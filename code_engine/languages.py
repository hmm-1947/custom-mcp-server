from pathlib import Path

from tree_sitter import Language, Parser

import tree_sitter_python as tspython
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
import tree_sitter_dart as tsdart

LANGUAGES = {
    ".py": Language(tspython.language()),
    ".java": Language(tsjava.language()),
    ".js": Language(tsjavascript.language()),
    ".ts": Language(tstypescript.language_typescript()),
    ".dart": Language(tsdart.language()),
}


def get_parser(file_path: str) -> Parser:
    suffix = Path(file_path).suffix.lower()

    if suffix not in LANGUAGES:
        raise ValueError(f"Unsupported language: {suffix}")

    parser = Parser()
    parser.language = LANGUAGES[suffix]

    return parser