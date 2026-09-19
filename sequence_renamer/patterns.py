"""Parse and render Nuke-style frame tokens in output patterns.

The module deliberately accepts one frame token per pattern.  Keeping that
constraint here, rather than in the UI, gives every caller the same safe and
predictable behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


class PatternError(ValueError):
    """Raised when a frame pattern cannot be interpreted unambiguously."""


MAX_PADDING_WIDTH = 255
_MAX_PADDING_WIDTH_TEXT = str(MAX_PADDING_WIDTH)


@dataclass(frozen=True, slots=True)
class FrameToken:
    """A frame token found in a pattern.

    ``start`` and ``end`` are Python slice offsets into the original pattern.
    ``width`` is ``None`` only for an unpadded ``%d`` token.  Alias tokens such
    as ``%04`` expose their canonical Nuke spelling through ``normalized``.
    """

    start: int
    end: int
    text: str
    width: int | None
    kind: str
    normalized: str

    @property
    def is_alias(self) -> bool:
        """Return whether this token uses the accepted shorthand syntax."""

        return self.kind == "printf_alias"


# The printf alternative is placed before the hash alternative only for
# readability; neither syntax can overlap the other.  The optional ``d`` makes
# ``%0N`` an alias while still consuming the complete canonical ``%0Nd`` form.
_FRAME_TOKEN_RE = re.compile(
    r"(?P<printf>%d|%0(?P<width>[1-9]\d*)(?P<specifier>d)?)|(?P<hashes>#+)"
)


def _validate_width_text(width_text: str) -> int:
    """Return a safe padding width without parsing unbounded integers."""

    maximum = _MAX_PADDING_WIDTH_TEXT
    if len(width_text) > len(maximum) or (
        len(width_text) == len(maximum) and width_text > maximum
    ):
        raise PatternError(
            f"Frame padding width cannot exceed {MAX_PADDING_WIDTH} characters."
        )
    return int(width_text)


def find_frame_tokens(pattern: str) -> tuple[FrameToken, ...]:
    """Return every supported frame token in *pattern*, from left to right.

    Supported spellings are ``#`` (one or more hashes), ``%d``, ``%0Nd`` and
    the convenience alias ``%0N``.  This discovery function does not require a
    token to exist; functions that consume a frame pattern do.
    """

    if not isinstance(pattern, str):
        raise TypeError("Pattern must be a string.")

    tokens: list[FrameToken] = []
    for match in _FRAME_TOKEN_RE.finditer(pattern):
        text = match.group(0)
        if match.group("hashes") is not None:
            if len(text) > MAX_PADDING_WIDTH:
                raise PatternError(
                    f"Frame padding width cannot exceed {MAX_PADDING_WIDTH} characters."
                )
            tokens.append(
                FrameToken(
                    start=match.start(),
                    end=match.end(),
                    text=text,
                    width=len(text),
                    kind="hash",
                    normalized=text,
                )
            )
            continue

        width_text = match.group("width")
        if width_text is None:
            width = None
            kind = "printf"
            normalized = "%d"
        else:
            width = _validate_width_text(width_text)
            is_alias = match.group("specifier") is None
            kind = "printf_alias" if is_alias else "printf"
            normalized = f"%0{width}d"

        tokens.append(
            FrameToken(
                start=match.start(),
                end=match.end(),
                text=text,
                width=width,
                kind=kind,
                normalized=normalized,
            )
        )

    return tuple(tokens)


def _require_single_token(pattern: str) -> FrameToken:
    tokens = find_frame_tokens(pattern)
    if not tokens:
        raise PatternError(
            "Pattern must contain exactly one frame token: #, %d, %0Nd, or %0N."
        )
    if len(tokens) > 1:
        raise PatternError(
            f"Pattern contains {len(tokens)} frame tokens; exactly one is required."
        )
    return tokens[0]


def normalize_pattern(pattern: str) -> str:
    """Return *pattern* with its single frame token in canonical form.

    Hash tokens and canonical printf tokens are unchanged.  The ``%0N`` alias
    is converted to ``%0Nd`` (for example, ``%04`` becomes ``%04d``).
    """

    token = _require_single_token(pattern)
    return f"{pattern[: token.start]}{token.normalized}{pattern[token.end :]}"


def render_frame_pattern(pattern: str, frame_number: int) -> str:
    """Render the single frame token in *pattern* for *frame_number*.

    Padding is a minimum field width, matching printf/Nuke behaviour.  It never
    truncates larger frame numbers, and the minus sign participates in the
    field width (``%04d`` renders ``-1`` as ``-001``).
    """

    if not isinstance(frame_number, int) or isinstance(frame_number, bool):
        raise TypeError("Frame number must be an integer.")

    token = _require_single_token(pattern)
    if token.width is None:
        rendered_frame = str(frame_number)
    else:
        rendered_frame = f"{frame_number:0{token.width}d}"

    return f"{pattern[: token.start]}{rendered_frame}{pattern[token.end :]}"


__all__ = [
    "FrameToken",
    "MAX_PADDING_WIDTH",
    "PatternError",
    "find_frame_tokens",
    "normalize_pattern",
    "render_frame_pattern",
]
