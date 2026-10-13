"""Provider-specific options, selected using the actual requested model."""


def thinking_options(model: str, *, enabled: bool = False) -> dict:
    if "qwen" in model.casefold():
        return {"extra_body": {"enable_thinking": enabled}}
    return {}
