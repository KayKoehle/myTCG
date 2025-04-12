from src.card_generator import generate_cards, plot_stats
from src.svg_generator import process_csv_with_template
from src.arrange_grid import arrange_svgs
from src.box_generator import create_box_from_template


if __name__ == "__main__":
    # generate_cards(file_path="./tables/locations.csv", output_path="./tables/cards_to_print.csv", edition="Alpha 0.1.0", artist="Sana:0.6b", writer="llama3.2:3b", print_all=False)

    # plot_stats("./tables/all_cards.csv", "all_cards")
    # plot_stats("./decklists/flow_of_the_currents.csv", "move_starter")
    # plot_stats("./decklists/echoes_of_the_storm.csv", "return_starter")
    # plot_stats("./decklists/unstoppable_growth.csv", "ramp_starter")
    # plot_stats("./decklists/swarming_nature.csv", "swarming_starter")
    # plot_stats("./decklists/flames_of_annihilation.csv", "destroy_starter")
    # plot_stats("./decklists/raging_fires.csv", "discard_starter")

    csv_file_path = "./tables/all_cards.csv"  # Path to your CSV file
    output_directory = "output_svgs"  # Directory where SVG files will be saved
    process_csv_with_template(csv_file_path, output_directory, color_print=True)
    arrange_svgs(input_dir = output_directory, output_dir = "print_svgs")

    # Create boxes
    # create_box_from_template(
    #     template_path="templates\\new_box_template.svg",
    #     output_path="output_boxes\\life_from_the_ashes.svg",
    #     deck_name="LIFE FROM THE ASHES",
    #     description="From ashes rises the power of rebirth! May your fallen creatures rise again.",
    #     image="..\\images\\color\\Gentle Priestess of Gaia.png",
    #     green=1,
    #     blue=0,
    #     red=0,
    #     colorless=0,
    # )

    # Create boosters
    # card_images = [
    #     "Curious Explorer"
    # ]
