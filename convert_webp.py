import os
from PIL import Image

def convert_webp_to_png(directory):
    if not os.path.isdir(directory):
        print(f"Directory '{directory}' does not exist.")
        return

    for filename in os.listdir(directory):
        if filename.lower().endswith(".webp"):
            webp_path = os.path.join(directory, filename)
            png_filename = os.path.splitext(filename)[0] + ".png"
            png_path = os.path.join(directory, png_filename)

            try:
                with Image.open(webp_path) as img:
                    img.save(png_path, "PNG")
                    print(f"Converted: {filename} -> {png_filename}")
            except Exception as e:
                print(f"Failed to convert {filename}: {e}")

# Example usage
if __name__ == "__main__":
    folder_path = "./images/color/creatures"
    convert_webp_to_png(folder_path)
