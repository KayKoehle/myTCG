import xml.etree.ElementTree as ET
import re

def embed_svg(target_root, source_svg_path, x_offset=0, y_offset=0, scale=1):
    source_tree = ET.parse(source_svg_path)
    source_root = source_tree.getroot()

    group_element = ET.Element(
        "g", {"transform": f"translate({x_offset},{y_offset}) scale({scale})"}
    )
    for child in source_root:
        group_element.append(child)
    target_root.append(group_element)


def get_element_position(element):
    x = element.get("x", "0")
    y = element.get("y", "0")

    transform = element.get("transform")
    if transform:
        translate_match = re.search(
            r"translate\((-?\d+\.?\d*),\s*(-?\d+\.?\d*)\)", transform
        )
        if translate_match:
            translate_x = float(translate_match.group(1))
            translate_y = float(translate_match.group(2))
            x = float(x) + translate_x
            y = float(y) + translate_y

    return x, y
