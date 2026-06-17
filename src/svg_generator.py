import csv
import os
import xml.etree.ElementTree as ET

from src.utils import delete_contents

from src.card_text_layout import layout_effect_and_lore


# ---------------------------------------------------------------------------
# Low-level SVG helpers
# ---------------------------------------------------------------------------

def fill_rectangle_with_image(
    root, namespace, image_path, id="image_frame", rotate=False
):
    if os.path.exists(image_path):
        print(f"Image path not found {image_path}")
        return

    rect_element = root.find(f".//svg:rect[@id='{id}']", namespace)
    if rect_element is not None:
        x = float(rect_element.attrib["x"])
        y = float(rect_element.attrib["y"])
        width = float(rect_element.attrib["width"])
        height = float(rect_element.attrib["height"])

        image_element = ET.Element(
            "image",
            {
                "href": image_path,
                "x": str(x),
                "y": str(y),
                "width": str(width),
                "height": str(height),
                "preserveAspectRatio": "xMidYMid slice",
            },
        )

        if rotate:
            center_x = x + width / 2
            center_y = y + height / 2
            image_element.attrib["transform"] = f"rotate(180, {center_x}, {center_y})"

        image_element.attrib["x"] = str(x)
        image_element.attrib["y"] = str(y)
        root.append(image_element)
    else:
        print("Could not fill image, rectangle not found")


def embed_colorless(target_root, source_svg_path, colorless, x_offset=0, y_offset=0):
    source_tree = ET.parse(source_svg_path)
    source_root = source_tree.getroot()

    group_element = ET.Element("g", {"transform": f"translate({x_offset},{y_offset})"})
    for child in source_root:
        group_element.append(child)
    target_root.append(group_element)

    namespace = {
        "svg": "http://www.w3.org/2000/svg",
        "inkscape": "http://www.inkscape.org/namespaces/inkscape",
        "sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    }
    ET.register_namespace("", namespace["svg"])
    ET.register_namespace("inkscape", namespace["inkscape"])
    ET.register_namespace("sodipodi", namespace["sodipodi"])

    number_element = target_root.find(".//svg:text[@id='number']", namespace)
    tspan = number_element.find(".//svg:tspan", namespace)
    tspan.text = str(colorless)


def embed_mana_icons(
    root, green, blue, red, colorless, color_print, x_offset, y_offset, rotate=False
):
    group_element = ET.Element("g")

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

    i = 0
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
        rotation_transform = (
            f"rotate(180, {x_offset + icon_width/2}, {y_offset + icon_height/2})"
        )
        group_element.set("transform", rotation_transform)

    root.append(group_element)


def get_svg_dimensions(svg_path):
    tree = ET.parse(svg_path)
    root = tree.getroot()

    width = root.attrib.get("width")
    height = root.attrib.get("height")

    if width is None or height is None:
        viewBox = root.attrib.get("viewBox")
        if viewBox:
            _, _, vb_width, vb_height = map(float, viewBox.split())
            return vb_width, vb_height
        else:
            raise ValueError(
                "Unable to determine dimensions: no width, height, or viewBox provided."
            )

    return float(width.replace("mm", "")), float(height.replace("mm", ""))


def delete_element(id: str, root, namespace):
    element_to_remove = root.find(f".//*[@id='{id}']", namespace)
    if element_to_remove is not None:
        for parent in root.iter():
            if element_to_remove in list(parent):
                parent.remove(element_to_remove)
                print(f"deleted {id}")
                break


def sanitize_element(elem):
    for key in list(elem.attrib):
        val = elem.attrib[key]
        if not isinstance(val, str):
            elem.attrib[key] = str(val)
    if elem.text is not None and not isinstance(elem.text, str):
        elem.text = str(elem.text)
    if elem.tail is not None and not isinstance(elem.tail, str):
        elem.tail = str(elem.tail)
    for child in elem:
        sanitize_element(child)


# ---------------------------------------------------------------------------
# Shared namespace + icon_map helpers
# ---------------------------------------------------------------------------

def _make_namespace():
    ns = {
        "svg": "http://www.w3.org/2000/svg",
        "inkscape": "http://www.inkscape.org/namespaces/inkscape",
        "sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    }
    ET.register_namespace("", ns["svg"])
    ET.register_namespace("inkscape", ns["inkscape"])
    ET.register_namespace("sodipodi", ns["sodipodi"])
    return ns


def _make_icon_map():
    return {
        "[1]": os.path.join("templates", "colorless.svg"),
        "[R]": os.path.join("templates", "color", "red.svg"),
        "[G]": os.path.join("templates", "color", "green.svg"),
        "[B]": os.path.join("templates", "color", "blue.svg"),
    }


# ---------------------------------------------------------------------------
# Card creators
# ---------------------------------------------------------------------------

def create_creature_card_from_template(
    template_path, output_path,
    name, main_type, subtype, effect,
    green, blue, red, colorless, power,
    edition, writer, artist,
    color_print=True, anecdote="",
):
    tree = ET.parse(template_path)
    root = tree.getroot()
    namespace = _make_namespace()

    title_element = root.find(".//svg:text[@id='title']", namespace)
    tspan = title_element.find(".//svg:tspan", namespace)
    tspan.text = name
    box_width = 51.0
    if len(name) * 3 > box_width:
        title_element.set("textLength", str(box_width))

    type_element = root.find(".//svg:text[@id='type']", namespace)
    type_element.find(".//svg:tspan", namespace).text = f"{main_type} — {subtype}"

    embed_mana_icons(root, green, blue, red, colorless, color_print,
                     x_offset=8, y_offset=1.9)

    effect_element = root.find(".//svg:text[@id='effect']", namespace)

    # ── CHANGED: single call handles both effect and lore dynamically ─────────
    layout_effect_and_lore(
        root, namespace, effect_element, effect, _make_icon_map(), anecdote
    )
    # ─────────────────────────────────────────────────────────────────────────

    root.find(".//svg:text[@id='power']", namespace).find(
        ".//svg:tspan", namespace).text = power
    root.find(".//svg:text[@id='edition']", namespace).find(
        ".//svg:tspan", namespace).text = edition
    root.find(".//svg:text[@id='writer']", namespace).find(
        ".//svg:tspan", namespace).text = writer
    root.find(".//svg:text[@id='artist']", namespace).find(
        ".//svg:tspan", namespace).text = artist

    image_path = os.path.join("..", "images", "color", "creatures", f"{name}.png")
    fill_rectangle_with_image(root, namespace, image_path)

    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def create_hero_card_from_template(
    template_path, output_path,
    name, main_type, subtype, effect,
    green, blue, red, colorless, power,
    edition, writer, artist,
    color_print=True, anecdote="",
):
    tree = ET.parse(template_path)
    root = tree.getroot()
    namespace = _make_namespace()

    if power == "0" or power == "":
        delete_element("power_star", root, namespace)
        delete_element("power", root, namespace)
        delete_element("title_power", root, namespace)
        delete_element("title_box_power", root, namespace)
        title_element = root.find(".//svg:text[@id='title_no_power']", namespace)
        title_element.find(".//svg:tspan", namespace).text = name
        box_width = 58.0
    else:
        root.find(".//svg:text[@id='power']", namespace).find(
            ".//svg:tspan", namespace).text = power
        delete_element("title_box_no_power", root, namespace)
        delete_element("title_no_power", root, namespace)
        title_element = root.find(".//svg:text[@id='title_power']", namespace)
        title_element.find(".//svg:tspan", namespace).text = name
        box_width = 51.0

    if len(name) * 3 > box_width:
        title_element.set("textLength", str(box_width))

    root.find(".//svg:text[@id='type']", namespace).find(
        ".//svg:tspan", namespace).text = f"{main_type} — {subtype}"

    embed_mana_icons(root, green, blue, red, colorless, color_print,
                     x_offset=2.2, y_offset=75.4)

    effect_element = root.find(".//svg:text[@id='effect']", namespace)

    # ── CHANGED ───────────────────────────────────────────────────────────────
    layout_effect_and_lore(
        root, namespace, effect_element, effect, _make_icon_map(), anecdote
    )
    # ─────────────────────────────────────────────────────────────────────────

    root.find(".//svg:text[@id='edition']", namespace).find(
        ".//svg:tspan", namespace).text = edition
    root.find(".//svg:text[@id='writer']", namespace).find(
        ".//svg:tspan", namespace).text = writer
    root.find(".//svg:text[@id='artist']", namespace).find(
        ".//svg:tspan", namespace).text = artist

    image_path = (
        os.path.join("..", "images", "color", "heroes", f"{name}.png") if color_print
        else os.path.join("..", "images", "blackWhite", "heroes", f"{name}.png")
    )
    fill_rectangle_with_image(root, namespace, image_path)

    sanitize_element(root)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def create_transform_card_from_template(
    template_path, output_path,
    name, main_type, subtype, effect,
    green, blue, red, colorless, power,
    edition, artist, writer,
    color_print=True, anecdote="",
):
    tree = ET.parse(template_path)
    root = tree.getroot()
    namespace = _make_namespace()

    try:
        names = name.split("#")
        effects = effect.split("#")
        powers = power.split("#")
        main_types = main_type.split("#")
        subtypes = subtype.split("#")
    except Exception:
        print(f"{name} missing some info about the transformed creature.")

    print(names)

    for slot, (title_id, type_id, effect_id, power_id) in enumerate([
        ("title",  "type",  "effect",  "power"),
        ("title2", "type2", "effect2", "power2"),
    ]):
        t = root.find(f".//svg:text[@id='{title_id}']", namespace)
        t.find(".//svg:tspan", namespace).text = names[slot]
        bw = 51.0
        if len(names[slot]) * 3 > bw:
            t.set("textLength", str(bw))

        root.find(f".//svg:text[@id='{type_id}']", namespace).find(
            ".//svg:tspan", namespace
        ).text = f"{main_types[slot]} — {subtypes[slot]}"

        effect_elem = root.find(f".//svg:text[@id='{effect_id}']", namespace)
        # Transform cards: only show anecdote on the front face (slot 0)
        slot_anecdote = anecdote if slot == 0 else ""
        layout_effect_and_lore(
            root, namespace, effect_elem, effects[slot], _make_icon_map(), slot_anecdote
        )

        root.find(f".//svg:text[@id='{power_id}']", namespace).find(
            ".//svg:tspan", namespace
        ).text = powers[slot]

    embed_mana_icons(root, green, blue, red, colorless, color_print,
                     x_offset=8, y_offset=1.9)

    for slot, img_id in enumerate(["image_frame", "image_frame2"]):
        color_dir = "color" if color_print else "blackWhite"
        img = os.path.join("..", "images", color_dir, "creatures", f"{names[slot]}.png")
        fill_rectangle_with_image(root, namespace, img, id=img_id, rotate=(slot == 1))

    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def create_location_card_from_template(
    template_path, output_path,
    name, effect,
    green, blue, red, colorless,
    edition, writer, artist,
    color_print=True, lore="",
):
    tree = ET.parse(template_path)
    root = tree.getroot()
    namespace = _make_namespace()

    for title_id in ("title", "title2"):
        t = root.find(f".//svg:text[@id='{title_id}']", namespace)
        t.find(".//svg:tspan", namespace).text = name
        if len(name) * 3 > 51.0:
            t.set("textLength", "51.0")

    if green + blue + red + colorless != 0:
        embed_mana_icons(root, green, blue, red, colorless, color_print,
                         x_offset=2.725, y_offset=63.563)
        embed_mana_icons(root, green, blue, red, colorless, color_print,
                         x_offset=58.277, y_offset=22.856, rotate=True)

    # Location cards: effect text is plain (no icon tokens), but lore still
    # participates in the dynamic layout.
    effect_element = root.find(".//svg:text[@id='effect']", namespace)
    layout_effect_and_lore(
        root, namespace, effect_element, effect, _make_icon_map(), lore
    )

    # Mirror effect to the second (rotated) face — no lore there
    effect_element2 = root.find(".//svg:text[@id='effect2']", namespace)
    if effect_element2 is not None:
        layout_effect_and_lore(
            root, namespace, effect_element2, effect, _make_icon_map(), ""
        )

    root.find(".//svg:text[@id='edition']", namespace).find(
        ".//svg:tspan", namespace).text = edition
    root.find(".//svg:text[@id='writer']", namespace).find(
        ".//svg:tspan", namespace).text = writer
    root.find(".//svg:text[@id='artist']", namespace).find(
        ".//svg:tspan", namespace).text = artist

    color_dir = "color" if color_print else "blackWhite"
    image_path = os.path.join("..", "images", color_dir, "locations", f"{name}.png")
    fill_rectangle_with_image(root, namespace, image_path)

    tree.write(output_path, encoding="utf-8", xml_declaration=True)


# ---------------------------------------------------------------------------
# CSV driver
# ---------------------------------------------------------------------------

def process_csv_with_template(csv_file_path: str, output_dir: str, color_print: bool):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    else:
        delete_contents(output_dir)

    with open(csv_file_path, mode="r", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        for idx, row in enumerate(reader):
            name      = row.get("Name", "").strip() or "Untitled"
            main_type = row.get("Type", "").strip()
            subtype   = row.get("Subtype", "").strip()
            effect    = row.get("Effect", "").strip() or "No Effect"
            green     = row.get("Green")
            blue      = row.get("Blue")
            red       = row.get("Red")
            colorless = row.get("Colorless")
            power     = row.get("Power")
            writer    = row.get("Writer", "").strip()
            edition   = row.get("Edition", "").strip()
            artist    = row.get("Artist", "").strip()
            anecdote  = (row.get("Lore") or row.get("Anecdote") or "").strip()

            output_path = os.path.join(output_dir, f"{name}.svg")

            if "Location" in main_type:
                create_location_card_from_template(
                    os.path.join("templates", "location_template.svg"),
                    output_path, name, effect,
                    int(green), int(blue), int(red), int(colorless),
                    edition, writer, artist, color_print, anecdote,
                )
            elif main_type == "Hero":
                create_hero_card_from_template(
                    os.path.join("templates", "hero_template.svg"),
                    output_path, name, main_type, subtype, effect,
                    int(green), int(blue), int(red), int(colorless),
                    power, edition, writer, artist, color_print, anecdote,
                )
            elif "#" in power:
                print(main_type, subtype)
                create_transform_card_from_template(
                    os.path.join("templates", "transform_template.svg"),
                    output_path, name, main_type, subtype, effect,
                    int(green), int(blue), int(red), colorless,
                    power, edition, writer, artist, color_print, anecdote,
                )
            else:
                create_creature_card_from_template(
                    os.path.join("templates", "creature_template.svg"),
                    output_path, name, main_type, subtype, effect,
                    int(green), int(blue), int(red), colorless,
                    power, edition, writer, artist, color_print, anecdote,
                )

            print(f"Card {idx+1} created: {output_path}")