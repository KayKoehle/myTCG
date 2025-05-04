import subprocess
import os
import xml.etree.ElementTree as ET
from src.utils import delete_contents


def svg_to_pdf(input_path, output_dir):
    # Full path to the Inkscape executable
    inkscape_path = r"C:\Program Files\Inkscape\bin\inkscape.exe"

    # Ensure the output directory exists
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Check if the input path is a file or a directory
    if os.path.isfile(input_path):
        # Handle single SVG file
        if input_path.lower().endswith(".svg"):
            output_pdf_path = os.path.join(
                output_dir, os.path.splitext(os.path.basename(input_path))[0] + ".pdf"
            )
            convert_svg(input_path, output_pdf_path)
        else:
            print(f"Error: The file {input_path} is not an SVG file.")
    elif os.path.isdir(input_path):
        # Handle directory containing SVG files
        for filename in os.listdir(input_path):
            if filename.lower().endswith(".svg"):
                input_svg_path = os.path.join(input_path, filename)
                output_pdf_path = os.path.join(
                    output_dir, os.path.splitext(filename)[0] + ".pdf"
                )
                convert_svg(input_svg_path, output_pdf_path)
    else:
        print(f"Error: The path {input_path} is neither a file nor a directory.")


def convert_svg(
    input_svg_path: str,
    output_pdf_path: str,
    output_type="pdf",
    inkscape_path: str = r"C:\Program Files\Inkscape\bin\inkscape.exe",
):
    """
    Convert a svg file to another file format like pdf or png.
    TODO: Get export type from output_path file ending
    """
    # Construct the command
    command = [
        inkscape_path,
        f"--export-type={output_type}",
        f"--export-filename={output_pdf_path}",
        input_svg_path,
    ]

    # Print the command for debugging
    print("Executing command:", " ".join(command))

    # Execute the command
    try:
        subprocess.run(command, check=True)
        print(f"PDF generated successfully: {output_pdf_path}")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Make sure the executable is installed and added to your system's PATH.")
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")
        print("The command failed. Check the command and try again.")


def export_to_tabletopsim(input_dir="output_svgs", output_dir="tabletopsim"):
    """
    Arrange .svg files from a directory into a grid of 10 columns x 7 rows
    on a DIN A4-sized SVG canvas. Embeds the SVG content directly into
    the grid with a fixed scale.
    """
    # First arrange the cards to the svg

    input_svgs = [
        os.path.join(input_dir, f) for f in os.listdir(input_dir) if f.endswith(".svg")
    ]
    if not input_svgs:
        raise ValueError("No SVG files found in the specified directory.")

    # DIN A4 dimensions in cm (21 x 29.7 cm)
    width = 108.373
    height = 108.373

    # Updated grid dimensions
    grid_cols, grid_rows = 10, 7
    svgs_per_page = grid_cols * grid_rows

    # Estimate cell size to fit 10x7 grid
    cell_width = width / grid_cols  # ~2.1 cm per card
    cell_height = height / grid_rows  # ~4.24 cm per card

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    else:
        delete_contents(output_dir)

    for page_num, i in enumerate(range(0, len(input_svgs), svgs_per_page)):
        target_svg_path = os.path.join(output_dir, f"tabletopsim_{page_num + 1}.svg")
        target_root = ET.Element(
            "svg",
            attrib={
                "xmlns": "http://www.w3.org/2000/svg",
                "width": f"{width}cm",
                "height": f"{height}cm",
                "viewBox": f"0 0 {width} {height}",
            },
        )

        background = ET.Element("rect", attrib={
            "x": "0", "y": "0",
            "width": f"{width}", "height": f"{height}",
            "fill": "white"
        })
        target_root.insert(0, background)

        for j, svg_file in enumerate(input_svgs[i : i + svgs_per_page]):
            row, col = divmod(j, grid_cols)
            x = col * cell_width
            y = row * cell_height

            with open(svg_file, "r", encoding="utf-8-sig") as file:
                svg_content = file.read()

            svg_element = ET.fromstring(svg_content)

            group_element = ET.Element(
                "g", attrib={"transform": f"translate({x} {y}) scale(0.1706661417322835 0.174962879640045)"}
            )

            for child in svg_element:
                group_element.append(child)

            target_root.append(group_element)

        tree = ET.ElementTree(target_root)
        tree.write(target_svg_path, encoding="utf-8-sig")

    # Convert svg to png
    for filename in os.listdir(output_dir):
        if filename.lower().endswith(".svg"):
            input_svg_path = os.path.join(output_dir, filename)
            output_pdf_path = os.path.join(
                output_dir, os.path.splitext(filename)[0] + ".png"
            )
            convert_svg(input_svg_path, output_pdf_path, output_type="png")