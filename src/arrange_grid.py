import os
from svgwrite import cm
from xml.etree import ElementTree as ET
from src.utils import delete_contents


def arrange_svgs(input_dir = "output_svgs", output_dir = "print_svgs"):
    """
    Arrange .svg files from a directory into a grid on a DIN A4-sized SVG canvas.
    Embeds the SVG content directly into the grid without scaling the original SVGs.

    :param input_dir: Directory containing input .svg files.
    :param output_dir: Directory to save the output .svg files.
    """
    # Get list of all .svg files in the directory
    input_svgs = [os.path.join(input_dir, f) for f in os.listdir(input_dir) if f.endswith('.svg')]
    if not input_svgs:
        raise ValueError("No SVG files found in the specified directory.")

    # DIN A4 dimensions in cm (21 x 29.7 cm)
    width = 21 
    height = 29.7 

    # Calculate grid cell size
    grid_cols, grid_rows = 3, 3
    cell_width = 6.350
    cell_height = 8.890
    svgs_per_page = grid_cols * grid_rows

    # Create output directory if it doesn't exist
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    else:
        delete_contents(output_dir)

    # Process SVGs and create grid files
    for page_num, i in enumerate(range(0, len(input_svgs), svgs_per_page)):
        # Parse the target root SVG structure
        target_svg_path = os.path.join(output_dir, f"output_grid_{page_num + 1}.svg")
        target_root = ET.Element("svg", attrib={
            "xmlns": "http://www.w3.org/2000/svg",
            "width": f"{width}cm",
            "height": f"{height}cm",
            "viewBox": f"0 0 {width} {height}"
        })

        for j, svg_file in enumerate(input_svgs[i:i + svgs_per_page]):
            row, col = divmod(j, grid_cols)
            x = (col * cell_width) + 0.9882
            y = (row * cell_height) + 1.5282

            # Parse the SVG content
            with open(svg_file, 'r', encoding="utf-8-sig") as file:
                svg_content = file.read()

            svg_element = ET.fromstring(svg_content)

            # Wrap the SVG content in a group element for positioning
            group_element = ET.Element("g", attrib={"transform": f"translate({x} {y})"})
            # group_element = ET.Element("g", attrib={"transform": f"translate({x} {y}) scale({0.01} {0.01})"})

            transform = group_element.get('transform', '')  
            new_transform = f'{transform} scale({0.1},{0.1})'.strip()
            group_element.set('transform', new_transform)

            # Wrap the SVG content in a group element for positioning
            # group_element = ET.Element("g")

            # Add all child elements of the SVG into the group
            for child in svg_element:
                group_element.append(child)

            # Add the group to the target root
            target_root.append(group_element)

        # Write the output SVG file
        tree = ET.ElementTree(target_root)
        tree.write(target_svg_path, encoding="utf-8-sig")
