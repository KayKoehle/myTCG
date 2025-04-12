import xml.etree.ElementTree as ET


def embed_svg(parent, source_svg_path, x_offset=0, y_offset=0):
    """
    Reads an SVG file and embeds it into the target SVG.
    :param parent: The parent element (e.g., <g> or <svg>) to embed the SVG into.
    :param source_svg_path: Path to the SVG file to embed.
    :param x_offset: Horizontal offset for positioning the embedded SVG.
    :param y_offset: Vertical offset for positioning the embedded SVG.
    """
    # Parse the source SVG
    source_tree = ET.parse(source_svg_path)
    source_root = source_tree.getroot()

    # Add a group element (<g>) to wrap the imported SVG content
    group_element = ET.Element(
        "g", {"transform": f"translate({x_offset},{y_offset}) scale(2)"}
    )

    # Append all children from the source SVG to the group
    for child in source_root:
        group_element.append(child)

    # Embed the group into the target SVG
    parent.append(group_element)


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
            raise ValueError("Unable to determine dimensions: no width, height, or viewBox provided.")

    # Convert width and height to float for consistency
    return float(width.replace("mm", "")), float(height.replace("mm", ""))


def embed_mana_icons(root, green, blue, red, colorless, position, rotate=False):
    # Create a group element to contain all the mana icons
    group_element = ET.Element("g")

    # List of colors and their counts
    mana_data = [
        ("./templates/box_icons/colorless.svg", colorless),
        ("./templates/box_icons/red.svg", red),
        ("./templates/box_icons/green.svg", green),
        ("./templates/box_icons/blue.svg", blue),
    ]

    # Variable to track the offset for each icon
    i = 0
    x_offset = position[0]
    y_offset = position[1]

    total_colors = green + blue + red
    if total_colors == 3:
        x_offset -= 6
    elif total_colors == 2:
        x_offset -= 3

    # Iterate over each color and count
    for color_file, count in mana_data:
        for _ in range(count):
            embed_svg(group_element, color_file, x_offset=x_offset + i * 6, y_offset=y_offset)
            i += 1

    # If rotation is requested, apply a rotate transform to the group
    if rotate:
        icon_width, icon_height = get_svg_dimensions("./templates/box_icons/red.svg")
        # Modify the transform attribute of the group to include rotation around the pivot point
        rotation_transform = f"rotate(180, {position[0] + icon_width}, {position[1] + icon_height})"
        group_element.set("transform", rotation_transform)

    root.append(group_element)


def create_box_from_template(
    template_path,
    output_path,
    deck_name,
    description,
    image,
    green,
    blue,
    red,
    colorless,
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

    if deck_name is not None:
        # Update the title
        title_element = root.find(".//svg:text[@id='front_title']", namespace)
        tspan = title_element.find(".//svg:tspan", namespace)
        tspan.text = deck_name

        # Update the top title
        top_element = root.find(".//svg:text[@id='top_title']", namespace)
        tspan = top_element.find(".//svg:tspan", namespace)
        tspan.text = deck_name

    if description is not None:
        # Update the description
        description_element = root.find(
            ".//svg:text[@id='explanation_text']", namespace
        )
        tspan = description_element.find(".//svg:tspan", namespace)
        tspan.text = description

    total_colors = green + blue + red
    if total_colors == 1:
        if green == 1:
            color = "green"
        elif blue == 1:
            color = "blue"
        else:
            color = "red"
    elif total_colors == 2:
        if green + blue == 2:
            color = "gb"
        elif red + blue == 2:
            color = "rb"
        elif green + red == 2:
            color = "rg"
    else:
        color = "rgb"

    # Update background color
    color_hex = {
        "red": "fill:#D40000",
        "rg": "fill:#975016",
        "green": "fill:#5AA02C",
        "gb": "fill:#019e91 ",
        "blue": "fill:#0000FF",
        "rb": "fill:#7F007F",
        "rgb": "fill:#3F403F",
    }
    background_element = root.find(".//svg:path[@id='background_color']", namespace)
    background_element.set("style", color_hex[color])

    # Add front icon
    if "booster" in template_path:
        position = (153.796, 114.528)
    else:
        position = (188.317, 144.245)
    embed_mana_icons(root, green, blue, red, colorless, position, rotate=False)

    # Change pattern image
    image_element = root.find(".//svg:image[@id='pattern_image']", namespace)
    image_element.set("href", image)

    # Write the modified SVG to the output path
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
