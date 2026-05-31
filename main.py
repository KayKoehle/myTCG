from src.card_generator import generate_cards, plot_stats
from src.svg_generator import process_csv_with_template
from src.arrange_grid import arrange_svgs
from src.box_generator import create_box_from_template
from src.pdf_export import svg_to_pdf
from src.comfyui_generator import generate_missing_images

if __name__ == "__main__":
    # svg_to_pdf(r"C:\Users\kayko\Documents\Python Projects\MyTCGCardGenerator\print_svgs", r"C:\Users\kayko\Documents\Python Projects\MyTCGCardGenerator\print_svgs")

    # generate_cards(file_path="./tables/all_cards.csv", output_path="./tables/cards_to_print.csv", edition="Alpha 0.1.0", artist="Sana:0.6b", writer="llama3.2:3b", print_all=True)

    # plot_stats("./tables/all_cards.csv", "all_cards")
    # plot_stats("./decklists/flow_of_the_currents.csv", "move_starter")
    # plot_stats("./decklists/echoes_of_the_storm.csv", "return_starter")
    # plot_stats("./decklists/unstoppable_growth.csv", "ramp_starter")
    # plot_stats("./decklists/swarming_nature.csv", "swarming_starter")
    # plot_stats("./decklists/flames_of_annihilation.csv", "destroy_starter")
    # plot_stats("./decklists/raging_fires.csv", "discard_starter")

    # csv_file_path = "tables/religion/mesopotamian/decklists/Epic_of_Gilgamesh.csv"  # Path to your CSV file
    # csv_file_path = "tables/religion/mesopotamian/decklists/Tiamats_Army.csv"  # Path to your CSV file
    # csv_file_path = "tables/religion/mesopotamian/decklists/Inannas_Descent_into_the_Underworld.csv"  # Path to your CSV file
    # csv_file_path = "tables/religion/mesopotamian/decklists/The_Flood.csv"  # Path to your CSV file
    csv_file_path = "tables/cards_to_print.csv"
    output_directory = "output_svgs"  # Directory where SVG files will be saved

    # --- Generate missing card art via ComfyUI (Flux2-Klein) ---
    # Uncomment to generate images for all cards in the CSV that don't have art yet.
    # Pass overwrite=True to regenerate images that already exist.
    # generate_missing_images(csv_file_path, output_base_dir="images/color", overwrite=False)

    process_csv_with_template(csv_file_path, output_directory, color_print=True)
    arrange_svgs(input_dir = output_directory, output_dir = "print_svgs")
    # export_to_tabletopsim()
    
    # Create boxes
    
    # create_box_from_template(
    #     template_path="templates\\slim_box_template.svg",
    #     output_path="output_boxes\\raging_fires_slim.svg",
    #     deck_name="RAGING FIRES",
    #     description="Set your hand ablaze and fuel your power! Toss aside your cards to unleash devastation effects, overwhelming your opponent with relentless aggression and fiery combos.",
    #     image="..\\images\\color\\creatures\\Kairos Ignis.png",
    #     green=1,
    #     blue=1,
    #     red=0,
    #     colorless=0,
    # )

    # create_box_from_template(
    #     template_path="templates\\slim_box_template.svg",
    #     output_path="output_boxes\\awaken_the_beast_slim.svg",
    #     deck_name="AWAKEN THE BEAST",
    #     description="Take control of your fate! Manipulate the top of your deck and unleash its power without cost.",
    #     image="..\\images\\color\\creatures\\Khaosian Chosen One.png",
    #     green=1,
    #     blue=1,
    #     red=0,
    #     colorless=0,
    # )
    
    # create_box_from_template(
    #     template_path="templates\\slim_box_template.svg",
    #     output_path="output_boxes\\life_from_the_ashes_slim.svg",
    #     deck_name="LIFE FROM THE ASHES",
    #     description="From the Ashes the power of revival emerges! May your fallen creatures rise again.",
    #     image="..\\images\\color\\creatures\\Flamewyrm.png",
    #     green=1,
    #     blue=0,
    #     red=1,
    #     colorless=0,
    # )

    # create_box_from_template(
    #     template_path="templates\\slim_box_template.svg",
    #     output_path="output_boxes\\tempest_of_flames_slim.svg",
    #     deck_name="TEMPEST OF FLAMES",
    #     description="Unleash a torrent of destruction! Combine the fury of fire with the unpredictability of arcane magic to decimate your opponents' forces..",
    #     image="..\\images\\color\\creatures\\Khaosflux.png",
    #     green=0,
    #     blue=1,
    #     red=1,
    #     colorless=0,
    # )

    # create_box_from_template(
    #     template_path="templates\\box_template.svg",
    #     output_path="output_boxes\\uncharted_frontiers.svg",
    #     deck_name="UNCHARTED FRONTIERS",
    #     description="Venture into uncharted realms where the very landscape shapes the battle! This expansion introduces dynamic locations that bestow unique effects, altering strategies and adding depth to every match.",
    #     image="..\\images\\color\\locations\\The Overlooked Corner.png",
    #     green=0,
    #     blue=0,
    #     red=0,
    #     colorless=0,
    # )

    # Create boosters
    # card_images = [
    #     "Curious Explorer"
    # ]
