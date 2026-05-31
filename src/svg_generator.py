import csv
import os
import xml.etree.ElementTree as ET
import re
from src.utils import delete_contents


# TODO place the frame ontop of the image.
def fill_rectangle_with_image(
    root, namespace, image_path, id="image_frame", rotate=False
):
    if os.path.exists(image_path):
        print(f"Image path not found {image_path}")
        return

    # Find the rectangle by its ID
    rect_element = root.find(f".//svg:rect[@id='{id}']", namespace)
    if rect_element is not None:
        # Get the dimensions of the rectangle
        x = float(rect_element.attrib["x"])
        y = float(rect_element.attrib["y"])

        width = float(rect_element.attrib["width"])
        height = float(rect_element.attrib["height"])

        # Add an <image> element to fill the rectangle
        image_element = ET.Element(
            "image",
            {
                "href": image_path,  # Path to the image
                "x": str(x),  # Position the image at the rectangle's x
                "y": str(y),  # Position the image at the rectangle's y
                "width": str(width),  # Set image width to match rectangle
                "height": str(height),  # Set image height to match rectangle
                "preserveAspectRatio": "xMidYMid slice",  # Preserve aspect ratio, crop if needed
            },
        )

        # Apply rotation if needed
        if rotate:
            # Calculate the center of the rectangle
            center_x = x + width / 2
            center_y = y + height / 2
            # Apply the rotation transformation
            image_element.attrib["transform"] = f"rotate(180, {center_x}, {center_y})"

        # ensure the image is ontop of the frame rectangle
        image_element.attrib["x"] = str(x)  # Position the image at the rectangle's x
        image_element.attrib["y"] = str(y)  # Position the image at the rectangle's y
        # Add the <image> element to the root of the SVG
        root.append(image_element)
        # print(f"Image {image_path} embedded")
    else:
        print("Could not fill image, rectangle not found")


def embed_colorless(target_root, source_svg_path, colorless, x_offset=0, y_offset=0):
    # Parse the source SVG
    source_tree = ET.parse(source_svg_path)
    source_root = source_tree.getroot()

    # Add a group element (<g>) to wrap the imported SVG content
    group_element = ET.Element("g", {"transform": f"translate({x_offset},{y_offset})"})

    # Append all children from the source SVG to the group
    for child in source_root:
        group_element.append(child)

    # Embed the group into the target SVG
    target_root.append(group_element)

    # Define the SVG namespace and register relevant namespaces
    namespace = {
        "svg": "http://www.w3.org/2000/svg",
        "inkscape": "http://www.inkscape.org/namespaces/inkscape",
        "sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    }
    ET.register_namespace("", namespace["svg"])
    ET.register_namespace("inkscape", namespace["inkscape"])
    ET.register_namespace("sodipodi", namespace["sodipodi"])

    # Update the number
    number_element = target_root.find(".//svg:text[@id='number']", namespace)
    tspan = number_element.find(".//svg:tspan", namespace)
    tspan.text = str(colorless)


def embed_svg(target_root, source_svg_path, x_offset=0, y_offset=0, scale=1):
    """
    Reads an SVG file and embeds it into the target SVG.
    :param target_root: The root of the target SVG.
    :param source_svg_path: Path to the SVG file to embed.
    :param x_offset: Horizontal offset for positioning the embedded SVG.
    :param y_offset: Vertical offset for positioning the embedded SVG.
    :param scale: Scale the target SVG by this value.
    """
    # Parse the source SVG
    source_tree = ET.parse(source_svg_path)
    source_root = source_tree.getroot()

    # Add a group element (<g>) to wrap the imported SVG content
    group_element = ET.Element(
        "g", {"transform": f"translate({x_offset},{y_offset}) scale({scale})"}
    )

    # Append all children from the source SVG to the group
    for child in source_root:
        group_element.append(child)

    # Embed the group into the target SVG
    target_root.append(group_element)


def embed_mana_icons(
    root, green, blue, red, colorless, color_print, x_offset, y_offset, rotate=False
):
    # Create a group element to contain all the mana icons
    group_element = ET.Element("g")

    # List of colors and their counts
    if color_print:
        mana_data = [
            ("./templates/colorless.svg", colorless),
            ("./templates/color/red.svg", red),
            ("./templates/color/green.svg", green),
            ("./templates/color/blue.svg", blue),
        ]
    else:
        mana_data = [
            ("./templates/colorless.svg", colorless),
            ("./templates/blackWhite/red.svg", red),
            ("./templates/blackWhite/green.svg", green),
            ("./templates/blackWhite/blue.svg", blue),
        ]

    if colorless == "0" and green + blue + red == 0:
        embed_colorless(
            group_element,
            "./templates/colorless.svg",
            colorless,
            x_offset=x_offset,
            y_offset=y_offset,
        )

    # Variable to track the offset for each icon
    i = 0

    # Iterate over each color and count
    for color_file, count in mana_data:
        if "colorless.svg" in color_file and (
            colorless == "X" or (isinstance(colorless, int) and colorless > 0)
        ):
            embed_colorless(
                group_element,
                color_file,
                colorless,
                x_offset=x_offset + i * 2.3,
                y_offset=y_offset,
            )
            i += 1
            continue
        elif "colorless.svg" in color_file and int(colorless) > 0:
            embed_colorless(
                group_element,
                color_file,
                colorless,
                x_offset=x_offset + i * 2.3,
                y_offset=y_offset,
            )
            i += 1
            continue

        for _ in range(int(count)):
            embed_svg(
                group_element,
                color_file,
                x_offset=x_offset + i * 2.3,
                y_offset=y_offset,
            )
            i += 1

    if rotate:
        icon_width, icon_height = get_svg_dimensions("./templates/color/red.svg")
        # Modify the transform attribute of the group to include rotation around the pivot point
        rotation_transform = (
            f"rotate(180, {x_offset + icon_width/2}, {y_offset + icon_height/2})"
        )
        group_element.set("transform", rotation_transform)

    root.append(group_element)


def get_svg_dimensions(svg_path):
    """
    Reads an SVG file and calculates its dimensions (width and height).
    :param svg_path: Path to the SVG file.
    :return: A tuple (width, height) representing the dimensions of the SVG.
    """
    # Parse the SVG file
    tree = ET.parse(svg_path)
    root = tree.getroot()

    # Extract width and height from the root element
    width = root.attrib.get("width")
    height = root.attrib.get("height")

    # If width and height are not explicitly set, calculate from viewBox
    if width is None or height is None:
        viewBox = root.attrib.get("viewBox")
        if viewBox:
            _, _, vb_width, vb_height = map(float, viewBox.split())
            return vb_width, vb_height
        else:
            raise ValueError(
                "Unable to determine dimensions: no width, height, or viewBox provided."
            )

    # Convert width and height to float for consistency
    return float(width.replace("mm", "")), float(height.replace("mm", ""))


def _render_lore(root, lore_text: str) -> None:
    """Append an italic lore/flavour text element to the SVG root."""
    if not lore_text:
        return
    lore_element = ET.Element(
        "text",
        {
            "xml:space": "preserve",
            "id": "lore",
            "x": "2.18",
            "y": "79",
            "style": (
                "font-size:2.82222px;font-style:italic;text-align:start;"
                "writing-mode:lr-tb;direction:ltr;text-anchor:start;"
                "white-space:pre;inline-size:58.4828;display:inline;"
                "fill:#555555;stroke:none;stroke-width:0.264583;"
                "-inkscape-font-specification:serif;font-family:serif;"
                "font-weight:normal;font-stretch:normal;font-variant:normal"
            ),
        },
    )
    tspan = ET.SubElement(lore_element, "tspan", {"x": "2.18", "y": "79"})
    tspan.text = lore_text
    root.append(lore_element)


def create_creature_card_from_template(
    template_path,
    output_path,
    name,
    main_type,
    subtype,
    effect,
    green,
    blue,
    red,
    colorless,
    power,
    edition,
    writer,
    artist,
    color_print=True,
    anecdote="",
):
    """Modifies an Inkscape SVG template to create a card."""
    # Parse the SVG template
    tree = ET.parse(template_path)
    root = tree.getroot()

    # Define the SVG namespace and register relevant namespaces
    namespace = {
        "svg": "http://www.w3.org/2000/svg",
        "inkscape": "http://www.inkscape.org/namespaces/inkscape",
        "sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    }
    ET.register_namespace("", namespace["svg"])
    ET.register_namespace("inkscape", namespace["inkscape"])
    ET.register_namespace("sodipodi", namespace["sodipodi"])

    # Update the title
    title_element = root.find(".//svg:text[@id='title']", namespace)
    tspan = title_element.find(".//svg:tspan", namespace)
    tspan.text = name

    # Make sure the title fits on the card
    box_width = 51.0
    approx_text_length = len(name) * 3
    if approx_text_length > box_width:
        title_element.set("textLength", str(box_width))

    # Update the type
    type_element = root.find(".//svg:text[@id='type']", namespace)
    tspan = type_element.find(".//svg:tspan", namespace)
    tspan.text = f"{main_type} — {subtype}"

    embed_mana_icons(
        root, green, blue, red, colorless, color_print, x_offset=8, y_offset=1.9
    )

    icon_map = {
        "[1]": os.path.join("templates", "colorless.svg"),
        "[R]": os.path.join("templates", "color", "red.svg"),
        "[G]": os.path.join("templates", "color", "green.svg"),
        "[B]": os.path.join("templates", "color", "blue.svg"),
    }

    # Update the effect
    effect_element = root.find(".//svg:text[@id='effect']", namespace)
    # tspan = effect_element.find(".//svg:tspan", namespace)
    # tspan.text = effect
    update_effect_text(root, namespace, effect_element, effect, icon_map)

    # Update the power
    power_element = root.find(".//svg:text[@id='power']", namespace)
    tspan = power_element.find(".//svg:tspan", namespace)
    tspan.text = power

    # Update the edition
    edition_element = root.find(".//svg:text[@id='edition']", namespace)
    tspan = edition_element.find(".//svg:tspan", namespace)
    tspan.text = edition

    # Update the LLM
    writer_element = root.find(".//svg:text[@id='writer']", namespace)
    tspan = writer_element.find(".//svg:tspan", namespace)
    tspan.text = writer

    # Update the artist
    artist_element = root.find(".//svg:text[@id='artist']", namespace)
    tspan = artist_element.find(".//svg:tspan", namespace)
    tspan.text = artist

    _render_lore(root, anecdote)

    if color_print:
        image_path = os.path.join("..", "images", "color", "creatures", f"{name}.png")
    else:
        image_path = os.path.join("..", "images", "color", "creatures", f"{name}.png")
    fill_rectangle_with_image(root, namespace, image_path)

    # Write the modified SVG to the output path
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def get_element_position(element):
    # Get x/y from <text> (default to 0 if not set)
    x = element.get("x", "0")
    y = element.get("y", "0")

    # Apply the transform (if it exists)
    transform = element.get("transform")
    if transform:
        # Parse the translate value from transform="translate(x,y)"
        translate_match = re.search(
            r"translate\((-?\d+\.?\d*),\s*(-?\d+\.?\d*)\)", transform
        )
        if translate_match:
            translate_x = float(translate_match.group(1))
            translate_y = float(translate_match.group(2))
            x = float(x) + translate_x  # Add the translate X
            y = float(y) + translate_y  # Add the translate Y

    return x, y


def update_effect_text(root, namespace, effect_element, effect_string, icon_map):
    """
    Replaces token placeholders like [G], [R], [B] in `effect_string` with <image> elements
    and appends plain text as <tspan> inside the given SVG text element.

    Parameters:
        - root
        - namespace (dict): XML namespace mapping for SVG and xlink
        - effect_element (Element): <text id="effect"> element
        - effect_string (str): Text containing placeholders to replace
        - icon_map (dict): Mapping from token (e.g., '[G]') to SVG filename
        - font_size (int): Font size used to estimate icon size and spacing
    """
    icon_size = 3.24
    tspan = effect_element.find(".//svg:tspan", namespace)
    x_pos, y_pos = get_element_position(effect_element)
    y_pos = float(y_pos) - 3.24
    x_offset, y_offset = 0, 0
    tspan.text = ""

    # Split into text and tokens
    parts = re.split(r"(\[G\]|\[R\]|\[B\]|\[1\])", effect_string)

    # Use the parent of <tspan> as the place to embed the icons
    group_element = ET.Element("g")

    for part in parts:
        if part in icon_map:
            while x_offset + icon_size > 58:
                x_offset -= 58
                y_offset += 5.29
            tspan.text += "   "
            icon_path = icon_map[part]
            embed_svg(
                group_element,
                icon_path,
                x_offset=x_pos + x_offset,
                y_offset=y_pos + y_offset,
                scale=1.3,
            )
            x_offset += icon_size
        elif part:
            tspan.text += part
            x_offset += 1.75 * len(part)  # estimated size of a character
    root.append(group_element)


def delete_element(id: str, root, namespace):
    element_to_remove = root.find(f".//*[@id='{id}']", namespace)
    if element_to_remove is not None:
        parent = (
            element_to_remove.getparent()
            if hasattr(element_to_remove, "getparent")
            else None
        )
        if parent is None:
            # If no parent (because ElementTree doesn't track parents), do it manually
            for parent in root.iter():
                if element_to_remove in list(parent):
                    parent.remove(element_to_remove)
                    print(f"deleted {id}")
                    break


def create_hero_card_from_template(
    template_path,
    output_path,
    name,
    main_type,
    subtype,
    effect,
    green,
    blue,
    red,
    colorless,
    power,
    edition,
    writer,
    artist,
    color_print=True,
    anecdote="",
):
    """Modifies an Inkscape SVG template to create a card."""
    # Parse the SVG template
    tree = ET.parse(template_path)
    root = tree.getroot()

    # Define the SVG namespace and register relevant namespaces
    namespace = {
        "svg": "http://www.w3.org/2000/svg",
        "inkscape": "http://www.inkscape.org/namespaces/inkscape",
        "sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    }
    ET.register_namespace("", namespace["svg"])
    ET.register_namespace("inkscape", namespace["inkscape"])
    ET.register_namespace("sodipodi", namespace["sodipodi"])

    if power == "0" or power == "":
        delete_element("power_star", root, namespace)
        delete_element("power", root, namespace)
        delete_element("title_power", root, namespace)
        delete_element("title_box_power", root, namespace)
        # Update the title
        title_element = root.find(".//svg:text[@id='title_no_power']", namespace)
        tspan = title_element.find(".//svg:tspan", namespace)
        tspan.text = name
        # Make sure the title fits on the card
        box_width = 58.0
    else:
        power_element = root.find(".//svg:text[@id='power']", namespace)
        tspan = power_element.find(".//svg:tspan", namespace)
        tspan.text = power
        delete_element("title_box_no_power", root, namespace)
        delete_element("title_no_power", root, namespace)
        # Update the title
        title_element = root.find(".//svg:text[@id='title_power']", namespace)
        tspan = title_element.find(".//svg:tspan", namespace)
        tspan.text = name
        # Make sure the title fits on the card
        box_width = 51.0
    approx_text_length = len(name) * 3
    if approx_text_length > box_width:
        title_element.set("textLength", str(box_width))

    # Update the type
    type_element = root.find(".//svg:text[@id='type']", namespace)
    tspan = type_element.find(".//svg:tspan", namespace)
    tspan.text = f"{main_type} — {subtype}"

    embed_mana_icons(
        root, green, blue, red, colorless, color_print, x_offset=2.2, y_offset=75.4
    )

    icon_map = {
        "[1]": os.path.join("templates", "colorless.svg"),
        "[R]": os.path.join("templates", "color", "red.svg"),
        "[G]": os.path.join("templates", "color", "green.svg"),
        "[B]": os.path.join("templates", "color", "blue.svg"),
    }

    # Update the effect
    effect_element = root.find(".//svg:text[@id='effect']", namespace)
    # tspan = effect_element.find(".//svg:tspan", namespace)
    # tspan.text = effect
    update_effect_text(root, namespace, effect_element, effect, icon_map)

    # Update the edition
    edition_element = root.find(".//svg:text[@id='edition']", namespace)
    tspan = edition_element.find(".//svg:tspan", namespace)
    tspan.text = edition

    # Update the LLM
    writer_element = root.find(".//svg:text[@id='writer']", namespace)
    tspan = writer_element.find(".//svg:tspan", namespace)
    tspan.text = writer

    # Update the artist
    artist_element = root.find(".//svg:text[@id='artist']", namespace)
    tspan = artist_element.find(".//svg:tspan", namespace)
    tspan.text = artist

    if color_print:
        image_path = os.path.join("..", "images", "color", "heroes", f"{name}.png")
    else:
        image_path = os.path.join("..", "images", "blackWhite", "heroes", f"{name}.png")
    fill_rectangle_with_image(root, namespace, image_path)

    _render_lore(root, anecdote)
    sanitize_element(root)
    # Write the modified SVG to the output path
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def sanitize_element(elem):
    # Clean attributes
    for key in list(elem.attrib):
        val = elem.attrib[key]
        if not isinstance(val, str):
            elem.attrib[key] = str(val)
    # Clean text
    if elem.text is not None and not isinstance(elem.text, str):
        elem.text = str(elem.text)
    if elem.tail is not None and not isinstance(elem.tail, str):
        elem.tail = str(elem.tail)
    # Recurse
    for child in elem:
        sanitize_element(child)


def create_transform_card_from_template(
    template_path,
    output_path,
    name,
    main_type,
    subtype,
    effect,
    green,
    blue,
    red,
    colorless,
    power,
    edition,
    artist,
    writer,
    color_print=True,
    anecdote="",
):
    """Modifies an Inkscape SVG template to create a card."""
    # Parse the SVG template
    tree = ET.parse(template_path)
    root = tree.getroot()

    # Define the SVG namespace and register relevant namespaces
    namespace = {
        "svg": "http://www.w3.org/2000/svg",
        "inkscape": "http://www.inkscape.org/namespaces/inkscape",
        "sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    }
    ET.register_namespace("", namespace["svg"])
    ET.register_namespace("inkscape", namespace["inkscape"])
    ET.register_namespace("sodipodi", namespace["sodipodi"])

    try:
        names = name.split("#")
        effects = effect.split("#")
        powers = power.split("#")
        main_types = main_type.split("#")
        subtypes = subtype.split("#")
    except:
        print(f"{name} missing some info about the transformed creature.")
    print(names)

    # Update the title
    title_element = root.find(".//svg:text[@id='title']", namespace)
    tspan = title_element.find(".//svg:tspan", namespace)
    tspan.text = names[0]

    # Make sure the title fits on the card
    box_width = 51.0
    approx_text_length = len(names[0]) * 3
    if approx_text_length > box_width:
        title_element.set("textLength", str(box_width))

    title_element = root.find(".//svg:text[@id='title2']", namespace)
    tspan = title_element.find(".//svg:tspan", namespace)
    tspan.text = names[1]

    box_width = 51.0
    approx_text_length = len(names[1]) * 3
    if approx_text_length > box_width:
        title_element.set("textLength", str(box_width))

    # Update the type
    type_element = root.find(".//svg:text[@id='type']", namespace)
    tspan = type_element.find(".//svg:tspan", namespace)
    tspan.text = f"{main_types[0]} — {subtypes[0]}"

    type_element = root.find(".//svg:text[@id='type2']", namespace)
    tspan = type_element.find(".//svg:tspan", namespace)
    tspan.text = f"{main_types[1]} — {subtypes[1]}"

    embed_mana_icons(
        root, green, blue, red, colorless, color_print, x_offset=8, y_offset=1.9
    )

    icon_map = {
        "[1]": os.path.join("templates", "colorless.svg"),
        "[R]": os.path.join("templates", "color", "red.svg"),
        "[G]": os.path.join("templates", "color", "green.svg"),
        "[B]": os.path.join("templates", "color", "blue.svg"),
    }

    # Update the effects
    effect_element = root.find(".//svg:text[@id='effect']", namespace)
    # tspan = effect_element.find(".//svg:tspan", namespace)
    # tspan.text = effects[0]
    print(f"EFFEKT TEXT {effects[0]}")
    update_effect_text(root, namespace, effect_element, effects[0], icon_map)

    effect_element = root.find(".//svg:text[@id='effect2']", namespace)
    # tspan = effect_element.find(".//svg:tspan", namespace)
    # tspan.text = effects[1]
    print(f"EFFEKT TEXT {effects[1]}")
    update_effect_text(root, namespace, effect_element, effects[1], icon_map)

    # Update the power
    power_element = root.find(".//svg:text[@id='power']", namespace)
    tspan = power_element.find(".//svg:tspan", namespace)
    tspan.text = powers[0]

    power_element = root.find(".//svg:text[@id='power2']", namespace)
    tspan = power_element.find(".//svg:tspan", namespace)
    tspan.text = powers[1]

    # # Update the edition
    # edition_element = root.find(".//svg:text[@id='edition']", namespace)
    # tspan = edition_element.find(".//svg:tspan", namespace)
    # tspan.text = edition

    # # Update the LLM
    # writer_element = root.find(".//svg:text[@id='writer']", namespace)
    # tspan = writer_element.find(".//svg:tspan", namespace)
    # tspan.text = writer

    # # Update the artist
    # artist_element = root.find(".//svg:text[@id='artist']", namespace)
    # tspan = artist_element.find(".//svg:tspan", namespace)
    # tspan.text = artist

    if color_print:
        image_path = os.path.join(
            "..", "images", "color", "creatures", f"{names[0]}.png"
        )
    else:
        image_path = os.path.join(
            "..", "images", "blackWhite", "creatures", f"{names[0]}.png"
        )
    fill_rectangle_with_image(root, namespace, image_path)

    if color_print:
        image_path = os.path.join(
            "..", "images", "color", "creatures", f"{names[1]}.png"
        )
    else:
        image_path = os.path.join(
            "..", "images", "blackWhite", "creatures", f"{names[1]}.png"
        )
    fill_rectangle_with_image(root, namespace, image_path, id="image_frame2", rotate=True)

    # Write the modified SVG to the output path
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def create_location_card_from_template(
    template_path,
    output_path,
    name,
    effect,
    green,
    blue,
    red,
    colorless,
    edition,
    writer,
    artist,
    color_print=True,
    lore="",
):
    """Modifies an Inkscape SVG template to create a card."""
    # Parse the SVG template
    tree = ET.parse(template_path)
    root = tree.getroot()

    # Define the SVG namespace and register relevant namespaces
    namespace = {
        "svg": "http://www.w3.org/2000/svg",
        "inkscape": "http://www.inkscape.org/namespaces/inkscape",
        "sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    }
    ET.register_namespace("", namespace["svg"])
    ET.register_namespace("inkscape", namespace["inkscape"])
    ET.register_namespace("sodipodi", namespace["sodipodi"])

    # Update the titles
    title_element = root.find(".//svg:text[@id='title']", namespace)
    tspan = title_element.find(".//svg:tspan", namespace)
    tspan.text = name

    title_element2 = root.find(".//svg:text[@id='title2']", namespace)
    tspan = title_element2.find(".//svg:tspan", namespace)
    tspan.text = name
    # Make sure the title fits on the card
    box_width = 51.0
    approx_text_length = len(name) * 3
    if approx_text_length > box_width:
        title_element.set("textLength", str(box_width))
        title_element2.set("textLength", str(box_width))

    if green + blue + red + colorless != 0:
        embed_mana_icons(
            root,
            green,
            blue,
            red,
            colorless,
            color_print,
            x_offset=2.725,
            y_offset=63.563,
        )
        embed_mana_icons(
            root,
            green,
            blue,
            red,
            colorless,
            color_print,
            x_offset=58.277,
            y_offset=22.856,
            rotate=True,
        )

    # Update the effects
    effect_element = root.find(".//svg:text[@id='effect']", namespace)
    tspan = effect_element.find(".//svg:tspan", namespace)
    tspan.text = effect

    effect_element2 = root.find(".//svg:text[@id='effect2']", namespace)
    tspan = effect_element2.find(".//svg:tspan", namespace)
    tspan.text = effect

    # Update the edition
    edition_element = root.find(".//svg:text[@id='edition']", namespace)
    tspan = edition_element.find(".//svg:tspan", namespace)
    tspan.text = edition

    # Update the LLM
    writer_element = root.find(".//svg:text[@id='writer']", namespace)
    tspan = writer_element.find(".//svg:tspan", namespace)
    tspan.text = writer

    # Update the artist
    artist_element = root.find(".//svg:text[@id='artist']", namespace)
    tspan = artist_element.find(".//svg:tspan", namespace)
    tspan.text = artist

    if color_print:
        image_path = os.path.join("..", "images", "color", "locations", f"{name}.png")
    else:
        image_path = os.path.join(
            "..", "images", "blackWhite", "locations", f"{name}.png"
        )
    fill_rectangle_with_image(root, namespace, image_path)

    _render_lore(root, lore)
    # Write the modified SVG to the output path
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def process_csv_with_template(csv_file_path: str, output_dir: str, color_print: bool):
    """Processes a CSV file to generate cards using an SVG template."""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    else:
        delete_contents(output_dir)

    with open(csv_file_path, mode="r", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        for idx, row in enumerate(reader):
            name = row.get("Name", "").strip() or "Untitled"
            main_type = row.get("Type", "").strip()
            subtype = row.get("Subtype", "").strip()
            effect = row.get("Effect", "").strip() or "No Effect"
            green = row.get("Green")
            blue = row.get("Blue")
            red = row.get("Red")
            colorless = row.get("Colorless")
            power = row.get("Power")
            writer = row.get("Writer").strip()
            edition = row.get("Edition").strip()
            artist = row.get("Artist").strip()
            anecdote = (row.get("Lore") or row.get("Anecdote") or "").strip()

            output_path = os.path.join(output_dir, f"{name}.svg")
            if "Location" in main_type:
                create_location_card_from_template(
                    os.path.join("templates", "location_template.svg"),
                    output_path,
                    name,
                    effect,
                    int(green),
                    int(blue),
                    int(red),
                    int(colorless),
                    edition,
                    writer,
                    artist,
                    color_print,
                    anecdote,
                )
            elif main_type == "Hero":
                create_hero_card_from_template(
                    os.path.join("templates", "hero_template.svg"),
                    output_path,
                    name,
                    main_type,
                    subtype,
                    effect,
                    int(green),
                    int(blue),
                    int(red),
                    int(colorless),
                    power,
                    edition,
                    writer,
                    artist,
                    color_print,
                    anecdote,
                )
            elif "#" in power:
                print(main_type)
                print(subtype)
                create_transform_card_from_template(
                    os.path.join("templates", "transform_template.svg"),
                    output_path,
                    name,
                    main_type,
                    subtype,
                    effect,
                    int(green),
                    int(blue),
                    int(red),
                    colorless,
                    power,
                    edition,
                    writer,
                    artist,
                    color_print,
                    anecdote,
                )
            else:
                create_creature_card_from_template(
                    os.path.join("templates", "creature_template.svg"),
                    output_path,
                    name,
                    main_type,
                    subtype,
                    effect,
                    int(green),
                    int(blue),
                    int(red),
                    colorless,
                    power,
                    edition,
                    writer,
                    artist,
                    color_print,
                    anecdote,
                )
            print(f"Card {idx+1} created: {output_path}")
