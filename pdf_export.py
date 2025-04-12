import subprocess

input_svg_path = "./output_svgs/output_grid_1.svg"
output_pdf_path = "./output_svgs/output_grid_1.pdf"

# Command to export SVG to PDF using Inkscape
command = [
    "inkscape",
    input_svg_path,
    "--export-type=pdf",
    "--export-filename=" + output_pdf_path
]

# Execute the command
subprocess.run(command, check=True)

print(f"Inkscape SVG successfully converted to PDF at {output_pdf_path}")