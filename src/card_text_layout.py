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

# Vertical text zone: top of effect area → bottom of card text zone
EFFECT_ZONE_TOP: float = 65.0    # y where effect text starts (approx)
CARD_TEXT_ZONE_BOTTOM: float = 93.0  # y of the lowest usable line

TOTAL_ZONE_HEIGHT: float = CARD_TEXT_ZONE_BOTTOM - EFFECT_ZONE_TOP  # ≈ 28 units

# Typography constants
NORMAL_EFFECT_FONT_SIZE: float = 2.82   # px / SVG units
NORMAL_LORE_FONT_SIZE: float = 2.50
SMALL_EFFECT_FONT_SIZE: float = 2.35
SMALL_LORE_FONT_SIZE: float = 2.10
TINY_EFFECT_FONT_SIZE: float = 1.95
TINY_LORE_FONT_SIZE: float = 1.75

CHAR_WIDTH_RATIO: float = 0.4 
LINE_HEIGHT_RATIO: float = 1.45  # line_height = font_size * ratio

LORE_SEPARATOR_GAP: float = 2.5  # vertical gap between effect block and lore

# Minimum lore font size before we drop lore entirely
LORE_MIN_FONT_SIZE: float = 1.60


# ---------------------------------------------------------------------------
# Helper: text-wrapping estimator
# ---------------------------------------------------------------------------

def _estimate_lines(text: str, font_size: float, col_width: float) -> int:
    """Return estimated number of wrapped lines for *text* at *font_size*."""
    if not text:
        return 0
    char_width = font_size * CHAR_WIDTH_RATIO
    chars_per_line = max(1, int(col_width / char_width))
    # Respect explicit newlines
    raw_lines = text.split("\n")
    total = 0
    for line in raw_lines:
        if len(line) == 0:
            total += 1
        else:
            total += math.ceil(len(line) / chars_per_line)
    return total


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

    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip() if current else word
        if len(candidate) <= chars_per_line:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    y = y_start
    for line in lines:
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

    parts = re.split(r"(\[G\]|\[R\]|\[B\]|\[1\])", effect_string)
    group_element = ET.Element("g")

    # Update font-size on the effect element itself
    style = effect_element.get("style", "")
    style = re.sub(r"font-size:[^;]+", f"font-size:{font_size}px", style)
    effect_element.set("style", style)

    col_width = TEXT_WIDTH
    char_width = font_size * CHAR_WIDTH_RATIO
    line_height = font_size * LINE_HEIGHT_RATIO

    for part in parts:
        if part in icon_map:
            while x_offset + icon_size > col_width:
                x_offset -= col_width
                y_offset += line_height
            tspan.text += "   "
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
    lines = _estimate_lines(effect_string, font_size, col_width)
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
        e_lines = _estimate_lines(effect, e_fs, TEXT_WIDTH)
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