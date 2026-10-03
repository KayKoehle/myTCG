"""
card_text_layout.py
-------------------
Dynamic layout engine for card effect text + lore/anecdote text.

Public API
----------
layout_effect_and_lore(root, namespace, effect_element, effect_string, icon_map, anecdote)
    → Call this instead of calling update_effect_text() and _render_lore() separately.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Card-specific constants (all in SVG user units / mm equivalents)
# ---------------------------------------------------------------------------

# Horizontal text column: x-start and usable width
TEXT_X: float = 2.18
TEXT_WIDTH: float = 58.0      # usable column width in SVG units

# Vertical text zone, in card (root) coordinates — the lore is appended to the
# root, so these must match creature_template.svg's frame: the effect text's
# first line sits at ≈55.4 and the effect_box ends at 85.24 (card: 88.9 tall).
EFFECT_ZONE_TOP: float = 55.4    # y where effect text starts (approx)
CARD_TEXT_ZONE_BOTTOM: float = 83.6  # y of the lowest usable baseline

TOTAL_ZONE_HEIGHT: float = CARD_TEXT_ZONE_BOTTOM - EFFECT_ZONE_TOP  # ≈ 29 units

# Typography constants
NORMAL_EFFECT_FONT_SIZE: float = 2.82   # px / SVG units
NORMAL_LORE_FONT_SIZE: float = 2.50
SMALL_EFFECT_FONT_SIZE: float = 2.35
SMALL_LORE_FONT_SIZE: float = 2.10
TINY_EFFECT_FONT_SIZE: float = 1.95
TINY_LORE_FONT_SIZE: float = 1.75

CHAR_WIDTH_RATIO: float = 0.43  # 0.4 let some italic lore lines overrun the box
# Inline mana icons are drawn over a run of spaces left in the effect text, at
# an x estimated from the preceding characters (regular, not italic, text).
EFFECT_CHAR_WIDTH_RATIO: float = 0.415
ICON_GAP: str = "     "  # wide enough for the icon (1.15 em) at ~0.25 em per space
LINE_HEIGHT_RATIO: float = 1.45  # line_height = font_size * ratio

LORE_SEPARATOR_GAP: float = 2.5  # vertical gap between effect block and lore

# Minimum lore font size before we drop lore entirely
LORE_MIN_FONT_SIZE: float = 1.60


# ---------------------------------------------------------------------------
# Keyword badges ("On enter:", "While on top:" ...)
# ---------------------------------------------------------------------------
# Mirrors the webapp (js/cardrefs.js): the trigger an effect starts with is
# drawn as a coloured pill with a small icon. (pattern, stroke, tint, text, icon)

KEYWORD_RE = re.compile(r"(^|(?<=[.!?]) +|(?<=\n))([A-Z][^:.\n]{2,60}):")

_ICON_STROKE = (' fill="none" stroke="{c}" stroke-width="1.5" '
                'stroke-linecap="round" stroke-linejoin="round"')

# 12x12 glyphs, same shapes as the webapp's ICONS. {c} is the stroke colour.
_ICON_PATHS = {
    "enter": '<path d="M1.5 6h5.5M4.8 3.5 7.3 6l-2.5 2.5M9.5 2v8"/>',
    "top": '<rect x="2" y="1.2" width="8" height="3.6" rx="1" fill="{c}" stroke="none"/>'
           '<path opacity=".6" d="M2.5 7.2h7M2.5 10h7"/>',
    "destroy": '<path d="M2.5 2.5l7 7M9.5 2.5l-7 7"/>',
    "revive": '<path d="M6 9V2.8M3.4 5.3 6 2.7l2.6 2.6M2.5 10.8h7"/>',
    "draw": '<rect x="2.8" y="1.2" width="6.4" height="9.6" rx="1.2"/>'
            '<path d="M4.6 5.3 6 6.8l1.4-1.5"/>',
    "once": '<path d="M10 6A4 4 0 1 1 8.6 3M8.8 1v2.3H6.5M5.4 5l.9-.6V8"/>',
    "timed": '<path d="M3.2 1.5h5.6M3.2 10.5h5.6M3.7 1.5c0 3 4.6 3 4.6 4.5s-4.6 1.5-4.6 4.5"/>'
             '<path d="M8.3 1.5c0 3-4.6 3-4.6 4.5s4.6 1.5 4.6 4.5"/>',
    "when": '<path d="M7 .8 2.8 6.6h2.8L5 11.2l4.4-6H6.6z" fill="{c}" stroke="none"/>',
}

# kind -> (matcher, stroke/text colour, pill tint). Darker than the webapp's
# colours: these sit on white paper, not a dark UI.
_KEYWORDS = [
    (re.compile(r"on enter", re.I), "enter", "#15803d", "#dcfce7"),
    (re.compile(r"while on top", re.I), "top", "#b45309", "#fef3c7"),
    (re.compile(r"on (destruction|death|leave)", re.I), "destroy", "#b91c1c", "#fee2e2"),
    (re.compile(r"on revive", re.I), "revive", "#0f766e", "#ccfbf1"),
    (re.compile(r"on draw", re.I), "draw", "#1d4ed8", "#dbeafe"),
    (re.compile(r"once per turn", re.I), "once", "#7e22ce", "#f3e8ff"),
    (re.compile(r"at the (start|end) of", re.I), "timed", "#c2410c", "#ffedd5"),
    (re.compile(r"when", re.I), "when", "#be185d", "#fce7f3"),
]


def _keyword_style(label: str):
    for pattern, kind, stroke, tint in _KEYWORDS:
        if pattern.match(label):
            return kind, stroke, tint
    return None


def _split_keywords(text: str) -> list:
    """Split *text* into plain strings and (label,) tuples for each keyword."""
    out: list = []
    pos = 0
    for m in KEYWORD_RE.finditer(text):
        if _keyword_style(m.group(2)) is None:
            continue
        start = m.start(2)
        if start > pos:
            out.append(text[pos:start])
        out.append((m.group(2),))
        pos = m.end()
    if pos < len(text):
        out.append(text[pos:])
    return out


def _estimate_text(text: str) -> str:
    """*text* with each keyword swapped for a same-width stand-in, so the line
    estimate accounts for the badge's icon and padding."""
    return "".join(f"xxx {p[0]}:" if isinstance(p, tuple) else p
                   for p in _split_keywords(text))


# ---------------------------------------------------------------------------
# Helper: text-wrapping estimator
# ---------------------------------------------------------------------------

def _estimate_lines(text: str, font_size: float, col_width: float) -> int:
    """Return estimated number of wrapped lines for *text* at *font_size*."""
    if not text:
        return 0
    char_width = font_size * CHAR_WIDTH_RATIO
    chars_per_line = max(1, int(col_width / char_width))
    # Respect explicit newlines; count words the way _wrap_words breaks them
    # (a plain len/width estimate undercounts and pushed lore into the footer).
    return sum(max(1, len(_wrap_words(line, chars_per_line))) for line in text.split("\n"))


def _wrap_words(text: str, chars_per_line: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = (current + " " + word).strip() if current else word
        if len(candidate) <= chars_per_line:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _lines_height(n_lines: int, font_size: float) -> float:
    return n_lines * font_size * LINE_HEIGHT_RATIO


# ---------------------------------------------------------------------------
# Helper: wrap text into tspan elements for SVG
# ---------------------------------------------------------------------------

def _append_wrapped_tspans(
    parent: ET.Element,
    text: str,
    x: float,
    y_start: float,
    font_size: float,
    col_width: float,
    fill: str = "#000000",
    italic: bool = False,
) -> float:
    """
    Append word-wrapped <tspan> children to *parent* starting at y_start.
    Returns the y coordinate just below the last line written.
    """
    char_width = font_size * CHAR_WIDTH_RATIO
    chars_per_line = max(1, int(col_width / char_width))
    line_height = font_size * LINE_HEIGHT_RATIO

    y = y_start
    for line in _wrap_words(text, chars_per_line):
        tspan = ET.SubElement(parent, "tspan", {
            "x": str(round(x, 4)),
            "y": str(round(y, 4)),
        })
        tspan.text = line
        y += line_height

    return y


# ---------------------------------------------------------------------------
# Internal: build the lore/flavour text SVG element
# ---------------------------------------------------------------------------

def _build_lore_element(
    text: str,
    x: float,
    y_start: float,
    font_size: float,
    col_width: float,
) -> ET.Element:
    """Return a fully-populated <text> element for lore text compatible with Inkscape."""
    style = (
        f"font-size:{font_size}px;"
        "font-style:italic;"
        "text-align:start;"
        "writing-mode:lr-tb;"
        "direction:ltr;"
        "text-anchor:start;"
        "white-space:pre;"
        "display:inline;"
        "fill:#555555;"
        "stroke:none;"
        f"stroke-width:0.264583;"
        "-inkscape-font-specification:serif;"
        "font-family:serif;"
        "font-weight:normal;"
        "font-stretch:normal;"
        "font-variant:normal"
    )
    elem = ET.Element("text", {
        "xml:space": "preserve",
        "id": "lore",
        "style": style,
    })
    _append_wrapped_tspans(elem, text, x, y_start, font_size, col_width,
                           italic=True, fill="#555555")
    return elem


# ---------------------------------------------------------------------------
# Internal: real font metrics for placing inline icons
# ---------------------------------------------------------------------------

# The template's effect text is font-family "serif" wrapped by Inkscape at this
# inline-size; on Windows Inkscape resolves "serif" to Times New Roman.
EFFECT_INLINE_SIZE: float = 58.4828
_EFFECT_FONT_FILES = ("times.ttf", "Times New Roman.ttf", "DejaVuSerif.ttf")
_MEASURE_PX = 100
_effect_font = None


def _load_effect_font():
    global _effect_font
    if _effect_font is None:
        _effect_font = False
        try:
            from PIL import ImageFont
        except ImportError:
            return None
        for name in _EFFECT_FONT_FILES:
            try:
                _effect_font = ImageFont.truetype(name, _MEASURE_PX)
                break
            except OSError:
                continue
    return _effect_font or None


def _caret_position(text: str, font_size: float) -> tuple[float, int] | None:
    """(x, line index) where the next character after *text* lands, wrapping
    words at EFFECT_INLINE_SIZE like Inkscape does. None if no font is found."""
    font = _load_effect_font()
    if font is None:
        return None

    def width(s: str) -> float:
        return font.getlength(s) * font_size / _MEASURE_PX

    line, line_idx = "", 0
    for token in re.split(r"( +)", text):
        if not token:
            continue
        if not token.startswith(" ") and line.strip() and width(line + token) > EFFECT_INLINE_SIZE:
            line, line_idx = "", line_idx + 1
        line += token
    return width(line), line_idx


def _text_width(text: str, font_size: float) -> float:
    font = _load_effect_font()
    if font is None:
        return len(text) * font_size * EFFECT_CHAR_WIDTH_RATIO
    return font.getlength(text) * font_size / _MEASURE_PX


def _draw_keyword_badge(group, tspan, label, font_size, x_pos, y_pos,
                        x_offset, y_offset, col_width, line_height):
    """Reserve room in *tspan* for a keyword pill and draw it into *group*.
    Returns the (x_offset, y_offset) the following text continues from."""
    kind, stroke, tint = _keyword_style(label)
    pad = font_size * 0.28
    icon = font_size * 0.78
    label_w = _text_width(label, font_size) * 1.07  # bold runs wider than regular
    pill_w = pad + icon + pad * 0.8 + label_w + pad
    pill_h = font_size * 1.1

    measured = _caret_position(tspan.text, font_size)
    if measured is not None:
        x_offset, line_idx = measured
        y_offset = line_idx * line_height
    while x_offset + pill_w > col_width:
        x_offset -= col_width
        y_offset += line_height
    x_offset = max(x_offset, 0.0)

    # Reserve the pill's width as spaces (a Times space is ~0.25 em wide).
    tspan.text += " " * math.ceil(pill_w / (font_size * 0.25))

    x0 = x_pos + x_offset
    top = y_pos + y_offset + font_size * 0.16
    ET.SubElement(group, "rect", {
        "x": f"{x0:.3f}", "y": f"{top:.3f}",
        "width": f"{pill_w:.3f}", "height": f"{pill_h:.3f}",
        "rx": f"{pill_h / 2:.3f}",
        "fill": tint, "stroke": stroke, "stroke-width": "0.18",
    })
    glyph = ET.SubElement(group, "g", {
        "transform": f"translate({x0 + pad + 0.1:.3f},{top + (pill_h - icon) / 2:.3f}) scale({icon / 12:.4f})",
        "fill": "none", "stroke": stroke, "stroke-width": "1.5",
        "stroke-linecap": "round", "stroke-linejoin": "round",
    })
    for el in ET.fromstring(
        "<g xmlns='http://www.w3.org/2000/svg'>" + _ICON_PATHS[kind].replace("{c}", stroke) + "</g>"
    ):
        glyph.append(el)
    text = ET.SubElement(group, "text", {
        "x": f"{x0 + pad + icon + pad * 0.8:.3f}",
        "y": f"{top + pill_h * 0.5 + font_size * 0.3:.3f}",
        "font-size": f"{font_size * 0.98:.3f}px",
        "font-family": "serif", "font-weight": "bold",
        "fill": stroke, "stroke": "none",
        "textLength": f"{label_w:.3f}", "lengthAdjust": "spacingAndGlyphs",
    })
    text.text = label
    return x_offset + pill_w, y_offset


# ---------------------------------------------------------------------------
# Internal: render effect text with inline icon support
# ---------------------------------------------------------------------------

def _render_effect(
    root: ET.Element,
    namespace: dict,
    effect_element: ET.Element,
    effect_string: str,
    icon_map: dict,
    font_size: float,
    y_override: float | None = None,
) -> float:
    """
    Render effect text into *effect_element*, inline-replacing icon tokens.
    Returns the y coordinate just below the last rendered line.
    """
    from src.svg_utils import embed_svg, get_element_position  # local imports

    icon_size = font_size * 1.15   # scale icon proportionally
    tspan = effect_element.find(".//svg:tspan", namespace)
    x_pos, y_pos = get_element_position(effect_element)
    if y_override is not None:
        y_pos = y_override
    else:
        y_pos = float(y_pos) - font_size

    x_offset, y_offset = 0.0, 0.0
    tspan.text = ""

    parts: list = []
    for piece in _split_keywords(effect_string):
        if isinstance(piece, tuple):
            parts.append(piece)
        else:
            parts.extend(re.split(r"(\[G\]|\[R\]|\[B\]|\[1\])", piece))
    group_element = ET.Element("g")

    # Update font-size on the effect element itself
    style = effect_element.get("style", "")
    style = re.sub(r"font-size:[^;]+", f"font-size:{font_size}px", style)
    effect_element.set("style", style)

    col_width = TEXT_WIDTH
    char_width = font_size * EFFECT_CHAR_WIDTH_RATIO
    line_height = font_size * LINE_HEIGHT_RATIO

    for part in parts:
        if isinstance(part, tuple):
            x_offset, y_offset = _draw_keyword_badge(
                group_element, tspan, part[0], font_size, x_pos, y_pos,
                x_offset, y_offset, col_width, line_height,
            )
        elif part in icon_map:
            measured = _caret_position(tspan.text, font_size)
            if measured is not None:
                # Real font metrics, replaying Inkscape's word wrap.
                x_offset, line_idx = measured
                y_offset = line_idx * line_height
            while x_offset + icon_size > col_width:
                x_offset -= col_width
                y_offset += line_height
            tspan.text += ICON_GAP
            scale = icon_size / 2.3   # 2.3 is the native icon unit size
            embed_svg(
                group_element,
                icon_map[part],
                x_offset=x_pos + x_offset,
                y_offset=y_pos + y_offset,
                scale=scale,
            )
            x_offset += icon_size
        elif part:
            tspan.text += part
            x_offset += char_width * len(part)

    root.append(group_element)

    # Estimate height consumed
    lines = _estimate_lines(_estimate_text(effect_string), font_size, col_width)
    return y_pos + _lines_height(lines, font_size)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class LayoutMode:
    LORE_PROMINENT = "lore_prominent"   # short/no effect → lore gets lots of space
    NORMAL = "normal"                    # both fit at normal size
    COMPACT = "compact"                  # both shrink a bit
    TINY = "tiny"                        # both shrink a lot
    NO_LORE = "no_lore"                  # lore hidden entirely


def _choose_layout(
    effect: str,
    anecdote: str,
) -> tuple[str, float, float]:
    """
    Decide layout mode + font sizes given raw text inputs.
    Returns (mode, effect_font_size, lore_font_size).
    """
    if not anecdote:
        return LayoutMode.NO_LORE, NORMAL_EFFECT_FONT_SIZE, 0.0

    # Try sizes from largest → smallest until everything fits
    for e_fs, l_fs in [
        (NORMAL_EFFECT_FONT_SIZE, NORMAL_LORE_FONT_SIZE),
        (SMALL_EFFECT_FONT_SIZE, SMALL_LORE_FONT_SIZE),
        (TINY_EFFECT_FONT_SIZE, TINY_LORE_FONT_SIZE),
    ]:
        e_lines = _estimate_lines(_estimate_text(effect), e_fs, TEXT_WIDTH)
        l_lines = _estimate_lines(anecdote, l_fs, TEXT_WIDTH)
        e_height = _lines_height(e_lines, e_fs)
        l_height = _lines_height(l_lines, l_fs)
        total = e_height + LORE_SEPARATOR_GAP + l_height

        if total <= TOTAL_ZONE_HEIGHT:
            if e_lines <= 2:
                return LayoutMode.LORE_PROMINENT, e_fs, l_fs
            elif e_fs == NORMAL_EFFECT_FONT_SIZE:
                return LayoutMode.NORMAL, e_fs, l_fs
            elif e_fs == SMALL_EFFECT_FONT_SIZE:
                return LayoutMode.COMPACT, e_fs, l_fs
            else:
                return LayoutMode.TINY, e_fs, l_fs

    # Nothing fits → drop lore
    return LayoutMode.NO_LORE, TINY_EFFECT_FONT_SIZE, 0.0


def layout_effect_and_lore(
    root: ET.Element,
    namespace: dict,
    effect_element: ET.Element,
    effect_string: str,
    icon_map: dict,
    anecdote: str,
) -> None:
    """
    Main entry point. Call instead of update_effect_text() + _render_lore().

    Dynamically sizes and positions effect text and lore/anecdote text so both
    fit within the card's text zone, degrading gracefully when content is long.
    """
    mode, e_fs, l_fs = _choose_layout(effect_string, anecdote)

    # --- Render effect text ---
    effect_bottom_y = _render_effect(
        root, namespace, effect_element, effect_string, icon_map, e_fs
    )

    if mode == LayoutMode.NO_LORE or not anecdote:
        return   # nothing more to do

    # --- Dynamic Positioning calculations ---
    l_lines = _estimate_lines(anecdote, l_fs, TEXT_WIDTH)
    l_height = _lines_height(l_lines, l_fs)

    if mode == LayoutMode.LORE_PROMINENT:
        # Scale up slightly if prominent
        l_fs_scaled = min(l_fs * 1.15, NORMAL_LORE_FONT_SIZE * 1.1)
        l_lines = _estimate_lines(anecdote, l_fs_scaled, TEXT_WIDTH)
        l_height = _lines_height(l_lines, l_fs_scaled)
        
        # In prominent mode, center it evenly inside the remaining box space below effect
        remaining_space = CARD_TEXT_ZONE_BOTTOM - (effect_bottom_y + LORE_SEPARATOR_GAP)
        lore_y = (effect_bottom_y + LORE_SEPARATOR_GAP) + max(0.0, (remaining_space - l_height) / 2)
        l_fs = l_fs_scaled
    else:
        # Standard Cards: Lock the bottom text line precisely up against CARD_TEXT_ZONE_BOTTOM
        lore_y = CARD_TEXT_ZONE_BOTTOM - l_height + (l_fs * LINE_HEIGHT_RATIO)

    # --- Append Lore ---
    lore_elem = _build_lore_element(anecdote, TEXT_X, lore_y, l_fs, TEXT_WIDTH)
    root.append(lore_elem)