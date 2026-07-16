from pydantic import BaseModel, ConfigDict


class SearchResultModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    file: str
    line: int
    end_line: int | None = None
    type: str | None = None
    name: str | None = None
    signature: str | None = None
    body: str | None = None
    text: str | None = None


SEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": SearchResultModel.model_json_schema(),
        }
    },
    "required": ["results"],
}

READ_FILES_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": {"type": "string"},
}

WORKSPACE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "workspaces": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
    },
}

EXPLORE_OUTPUT_SCHEMA = {"type": "object", "additionalProperties": True}

EDIT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "context": {"type": "string"},
        "affected_lines": {"type": "object"},
        "previous_line_edit": {"type": "object"},
        "validation": {"type": "object"},
        "dry_run": {"type": "boolean"},
    },
    "required": ["message"],
}
