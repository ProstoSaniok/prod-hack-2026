def utf8_text(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Expected UTF-8 text") from None
    if "\x00" in value:
        raise ValueError("Text cannot contain NUL")
    return value
