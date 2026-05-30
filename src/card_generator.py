import ollama
import csv
from itertools import product
import hashlib
import base64
import matplotlib.pyplot as plt
import os


def generate_card_id(red, green, blue, colorless, power, effect) -> str:
    # Use base64 to hash the effect text
    text = f"{red}{green}{blue}{colorless}{power}{effect}"
    hash_object = hashlib.sha256(text.encode("utf-8"))
    hash_bytes = hash_object.digest()  # Get raw bytes
    return base64.urlsafe_b64encode(hash_bytes).decode("utf-8")[:11]  # Take 11 chars


def plot_color_stats(cards, filename, save_dir):
    """
    Prints stats about a list of card dictionaries and plots a mana distribution curve.

    Args:
        cards (list): List of card dictionaries.
    """
    total_cards = len(cards)
    mana_distribution = {
        "Red": 0,
        "RG": 0,
        "Green": 0,
        "GB": 0,
        "Blue": 0,
        "BR": 0,
        "RGB": 0,
        "Colorless": 0,
    }

    for card in cards:
        if int(card["Red"]) > 0 and int(card["Green"]) > 0 and int(card["Blue"]) > 0:
            mana_distribution["RGB"] += 1
        elif int(card["Red"]) > 0 and int(card["Green"]) > 0:
            mana_distribution["RG"] += 1
        elif int(card["Green"]) > 0 and int(card["Blue"]) > 0:
            mana_distribution["GB"] += 1
        elif int(card["Blue"]) > 0 and int(card["Red"]) > 0:
            mana_distribution["BR"] += 1
        elif int(card["Red"]) > 0:
            mana_distribution["Red"] += 1
        elif int(card["Green"]) > 0:
            mana_distribution["Green"] += 1
        elif int(card["Blue"]) > 0:
            mana_distribution["Blue"] += 1
        else:
            mana_distribution["Colorless"] += 1

    print("\nMana color distribution:")
    print(total_cards)
    for color, mana in mana_distribution.items():
        print(f"  {color}: {mana}")

    # Plot mana distribution curve
    colors = list(mana_distribution.keys())
    mana_values = list(mana_distribution.values())

    plt.clf()
    bars = plt.bar(
        colors,
        mana_values,
        color=["red", "yellow", "green", "cyan", "blue", "magenta", "black", "gray"],
    )

    # Add count labels on top of each bar
    for bar in bars:
        height = bar.get_height()
        plt.text(
            bar.get_x() + bar.get_width() / 2,  # X-coordinate (center of the bar)
            height + 0.1,  # Y-coordinate (slightly above the bar)
            f"{int(height)}",  # Text to display (count)
            ha="center",
            va="bottom",  # Horizontal and vertical alignment
        )

    plt.title("Mana Color Distribution")
    plt.xlabel("Mana Color")
    plt.ylabel("Number of cards")
    os.makedirs(save_dir, exist_ok=True)
    plot_path = os.path.join(save_dir, f" {filename} mana_color_distribution.png")
    plt.savefig(plot_path)
    print(f"\nPlot saved to {plot_path}")


def plot_mana_cost_distribution(cards, filename, save_dir=None):
    """
    Plots the mana cost distribution curve (total mana cost of each card).
    Saves the plot to a specified directory.

    Args:
        cards (list): List of card dictionaries.
        save_dir (str): Directory to save the plot. If None, the plot is shown but not saved.
    """
    # Calculate total mana cost for each card
    mana_costs = []
    for card in cards:
        green = int(card["Green"])
        blue = int(card["Blue"])
        red = int(card["Red"])
        if card["Colorless"] == "X":
            colorless = 0
        else:
            colorless = int(card["Colorless"])
        mana_costs.append(green + blue + red + colorless)

    # Count how many cards have each total mana cost
    max_cost = max(mana_costs)
    cost_distribution = [0] * (
        max_cost + 1
    )  # Array to store counts of mana costs from 0 to max_cost

    for cost in mana_costs:
        cost_distribution[cost] += 1

    # Plot mana cost distribution curve
    x = list(range(len(cost_distribution)))  # Mana costs (0, 1, 2, ..., max_cost)
    y = cost_distribution  # Number of cards with each mana cost

    plt.clf()
    bars = plt.bar(x, y, color="purple")
    # Add count labels on top of each bar
    for bar in bars:
        height = bar.get_height()
        plt.text(
            bar.get_x() + bar.get_width() / 2,  # X-coordinate (center of the bar)
            height + 0.1,  # Y-coordinate (slightly above the bar)
            f"{int(height)}",  # Text to display (count)
            ha="center",
            va="bottom",  # Horizontal and vertical alignment
        )

    plt.title("Mana Cost Distribution")
    plt.xlabel("Total Mana Cost")
    plt.ylabel("Number of Cards")
    plt.xticks(x)  # Show every mana cost as a tick on the x-axis

    os.makedirs(save_dir, exist_ok=True)
    plot_path = os.path.join(save_dir, f"{filename} mana_cost_distribution_curve.png")
    plt.savefig(plot_path)
    print(f"\nPlot saved to {plot_path}")


def plot_effect_chart(all_cards, filename, save_dir):
    """
    Plots a pie chart with card effects.
    Chart shows how many 'On Play', 'On Destruction', 'On Return', 'On Revive', 'On Discard' effects there are.

    Args:
        cards (list): List of card dictionaries.
        save_dir (str): Directory to save the plot. If None, the plot is shown but not saved.
    """
    plt.clf()
    effect_types = {
        "On Play": 0,
        "On Destruction": 0,
        "On Return": 0,
        "On Revive": 0,
        "On Discard": 0,
        "While in your graveyard": 0,
        "No Effect": 0,
        "On Draw": 0,
        "On Move": 0,
        "Location": 0,
        "Equipment": 0,
        "Other": 0,
    }

    for card in all_cards:
        if card["Type"] == "Location":
            effect_types["Location"] += 1
        if card["Type"] == "Equipment":
            effect_types["Equipment"] += 1
        else:
            if card["Effect"].startswith("On Play"):
                effect_types["On Play"] += 1
            elif card["Effect"].startswith("On Destruction"):
                effect_types["On Destruction"] += 1
            elif card["Effect"].startswith("On Return"):
                effect_types["On Return"] += 1
            elif card["Effect"].startswith("On Revive"):
                effect_types["On Revive"] += 1
            elif card["Effect"].startswith("On Discard"):
                effect_types["On Discard"] += 1
            elif card["Effect"].startswith("While in your graveyard"):
                effect_types["While in your graveyard"] += 1
            elif card["Effect"] == "":
                effect_types["No Effect"] += 1
            elif card["Effect"].startswith("On Draw"):
                effect_types["On Draw"] += 1
            elif card["Effect"].startswith("On Move"):
                effect_types["On Move"] += 1
            else:
                effect_types["Other"] += 1

    # Remove effect types with zero counts for cleaner pie chart
    effect_types = {key: value for key, value in effect_types.items() if value > 0}

    def absolute_number(pct, all_vals):
        total = sum(all_vals)
        count = int(round(pct * total / 100.0))
        return f"{count}"

    # Create a pie chart
    fig, ax = plt.subplots()
    ax.pie(
        effect_types.values(),
        labels=effect_types.keys(),
        autopct=lambda pct: absolute_number(pct, list(effect_types.values())),
        startangle=90,  # Start pie chart from the top
        colors=plt.cm.tab20.colors[
            : len(effect_types)
        ],  # Use a colormap for varied colors
    )

    ax.set_title("Card Effect Distribution")

    # Save the plot to the specified directory
    os.makedirs(save_dir, exist_ok=True)
    plot_path = os.path.join(save_dir, f"{filename} effect_chart.png")
    plt.savefig(plot_path)
    print(f"\nPlot saved to {plot_path}")
    plt.close(fig)  # Close the figure to free up memory


def generate_creature_name(
    type: str,
    green: int,
    blue: int,
    red: int,
    colorless: int,
    power: int,
    effect: str,
    names: set,
) -> str:
    """
    Uses a language model to generate a name given the mana cost, power and effect.
    """
    MAX_ATTEMPTS = 15
    for i in range(MAX_ATTEMPTS):
        response = ollama.chat(
            model="llama3.2:3b",
            messages=[
                {
                    "role": "user",
                    "content": f"I am designing a trading card game. Please give me a flavourful title for a creature card of type {type} which costs {green} green mana, {blue} blue mana, {red} red mana and {colorless} colorless mana. It has {power} power. The power of cards in this game go up to around 15, so the title should reflect the power level. For example creatures with power one should be tiny like squirrels and ants and creatures with power 15 should be godlike creatures. The effect of the card is: {effect} Only answer with the title and nothing else. The title should not contain a name. Dont use words already in the effect.",
                }
            ],
        )
        name = response.message.content
        if name not in names:
            break
        print(f"Attempt Number {i}")
    else:
        raise ValueError(
            f"Unable to generate a unique name after {MAX_ATTEMPTS} attempts"
        )

    print(f"Generated card name {name}")
    return name

def generate_transform_name(
    type: str,
    green: int,
    blue: int,
    red: int,
    colorless: int,
    power: int,
    effect: str,
    names: set,
) -> tuple:
    """
    Uses a language model to generate a two names for a transforming card given the mana cost, powers and effects.
    """
    types = type.split("#")
    powers = power.split("#")
    effects = effect.split("#")

    MAX_ATTEMPTS = 15
    for i in range(MAX_ATTEMPTS):
        response = ollama.chat(
            model="llama3.2:3b",
            messages=[
                {
                    "role": "user",
                    "content": f"I am designing a trading card game. Please give me a flavourful title for a creature card of type {types[0]} which costs {green} green mana, {blue} blue mana, {red} red mana and {colorless} colorless mana. It has {powers[0]} power. The power of cards in this game go up to around 15, so the title should reflect the power level. For example creatures with power one should be tiny like squirrels and ants and creatures with power 15 should be godlike creatures. The effect of the card is: {effects[0]} The creature can transform/ evolve. Only answer with the title and nothing else. The title should not contain a name. Dont use words already in the effect.",
                }
            ],
        )
        name1 = response.message.content
        if name1 not in names:
            break
        print(f"Attempt Number {i}")
    else:
        raise ValueError(
            f"Unable to generate a unique name after {MAX_ATTEMPTS} attempts"
        )
    
    for i in range(MAX_ATTEMPTS):
        response = ollama.chat(
            model="llama3.2:3b",
            messages=[
                {
                    "role": "user",
                    "content": f"I am designing a trading card game. Please give me a flavourful title for a creature card of type {types[0]} which costs {green} green mana, {blue} blue mana, {red} red mana and {colorless} colorless mana. It has {powers[0]} power. The power of cards in this game go up to around 15, so the title should reflect the power level. For example creatures with power one should be tiny like squirrels and ants and creatures with power 15 should be godlike creatures. The effect of the card is: {effects[0]} The creature is transformed/ evolved version of a creature named {name1}. Only answer with the title and nothing else. The title should not contain a name. Dont use words already in the effect.",
                }
            ],
        )
        name2 = response.message.content
        if name2 not in names:
            break
        print(f"Attempt Number {i}")
    else:
        raise ValueError(
            f"Unable to generate a unique name after {MAX_ATTEMPTS} attempts"
        )
    

    print(f"Generated card names {name1}, {name2}")
    return name1, name2



def generate_hero_name(names: set) -> tuple:
    """
    Uses a language model to generate a name given the mana cost, power and effect.
    """
    MAX_ATTEMPTS = 15
    for i in range(MAX_ATTEMPTS):
        response = ollama.chat(
            model="llama3.2:3b",
            messages=[
                {
                    "role": "user",
                    "content": f"Give ma an epic sounding fictional name for a hero. It is not necessarily human. Answer with only the name and nothing else.",
                }
            ],
        )
        name = response.message.content
        if name not in names:
            break
        print(f"Attempt Number {i}")
    else:
        raise ValueError(
            f"Unable to generate a unique name after {MAX_ATTEMPTS} attempts"
        )

    print(f"Generated card name {name}")
    return name


def generate_location_name(
    green: int, blue: int, red: int, colorless: int, effect: str, names: set
) -> tuple:
    """
    Uses a language model to generate a name given the mana cost, power and effect.
    """
    MAX_ATTEMPTS = 15
    for i in range(MAX_ATTEMPTS):
        response = ollama.chat(
            model="llama3.2:3b",
            messages=[
                {
                    "role": "user",
                    "content": f"I am designing a trading card game. Please give me a flavourful name for a location card which costs {green} green mana, {blue} blue mana, {red} red mana and {colorless} colorless mana. The effect of the card is: {effect} Only answer with the name and nothing else. Dont use words already in the effect. Since it's a location card the card name should be something like a building or a landscape.",
                }
            ],
        )
        name = response.message.content
        if name not in names:
            break
        print(f"Attempt Number {i}")
    else:
        raise ValueError(
            f"Unable to generate a unique name after {MAX_ATTEMPTS} attempts"
        )

    print(f"Generated card name {name}")
    return name


def generate_equipment_name(
    green: int, blue: int, red: int, colorless: int, effect: str, names: set
) -> tuple:
    """
    Uses a language model to generate a name given the mana cost, power and effect.
    """
    MAX_ATTEMPTS = 15
    for i in range(MAX_ATTEMPTS):
        response = ollama.chat(
            model="llama3.2:3b",
            messages=[
                {
                    "role": "user",
                    "content": f"I am designing a trading card game. Please give me a flavourful name for a equipment card which costs {green} green mana, {blue} blue mana, {red} red mana and {colorless} colorless mana. The effect of the card is: {effect} Only answer with the name and nothing else. Dont use words already in the effect. Since it's a equipment card the card name should be something like a weapon or a wearable thing.",
                }
            ],
        )
        name = response.message.content
        if name not in names:
            break
        print(f"Attempt Number {i}")
    else:
        raise ValueError(
            f"Unable to generate a unique name after {MAX_ATTEMPTS} attempts"
        )

    print(f"Generated card name {name}")
    return name


def generate_subtype(card_type: str) -> tuple:
    """
    Uses a language model to generate a type given the card type.
    """
    response = ollama.chat(
        model="llama3.2:3b",
        messages=[
            {
                "role": "user",
                "content": f"I am designing a trading card game. Please give me a {card_type} type for a {card_type} card. Take inspiration from fantasy and all kinds of mythology. Only answer with the type and nothing else.",
            }
        ],
    )
    print(f"Generated subtype {response.message.content}")
    return response.message.content


def generate_subtype_from_name(card_type: str, name: str):
    """
    Uses a language model to generate a type given the card_type and name.
    """
    response = ollama.chat(
        model="llama3.2:3b",
        messages=[
            {
                "role": "user",
                "content": f"I am designing a trading card game. Please give me a type for a {card_type} card. The {card_type}'s name is {name}. Take inspiration from fantasy and all kinds of mythology. It must not be a Kitsune. Only answer with the type and nothing else.",
            }
        ],
    )
    print(f"Generated subtype {response.message.content} from name {name}")
    return response.message.content


def read_csv(file_path: str) -> dict:
    data = []
    with open(file_path, mode="r", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            data.append(dict(row))
    return data


def write_csv(cards: list, file_name: str = "new_cards.csv"):
    """
    Saves the generated creature cards into a csv file.

    creatures (list): List of dictionaries. {"Title": "Creature Name", "Type": "Creature Type", "Effect": "Effect", "Green": 1, "Blue": 1, "Red": 1, "Power": 1, "Artist": None, "Edition": None}
    """
    if cards == []:
        return

    # Get the keys from the dictionary as column headers
    headers = cards[0].keys()

    # Write to CSV file
    with open(file_name, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=headers)

        # Write the header row
        writer.writeheader()

        # Write the data rows
        writer.writerows(cards)

    print(f"Creatures successfully saved to {file_name}.")


def id_exists(id: str, cards: list) -> bool:
    for card in cards:
        if id == card["ID"]:
            return True
    return False


def generate_cards(
    file_path: str,
    output_path: str,
    edition: str,
    artist: str,
    writer: str,
    print_all: bool = False,
):
    """
    Reads a .csv with cards. If print_all is False, only writes entries into an output_path csv if the card is new or changed.
    """
    card_table = read_csv(file_path)

    names = set()  # Make sure no card name is duplicated
    for card in card_table:
        if card["Name"] != "":
            names.add(card["Name"])

    all_cards = []
    new_cards = []
    # fill in missing card info in new cards like name. check with hash for any changes.
    for card in card_table:
        # if card["Subtype"] == "" and card["Name"] == "":
        #     card["Subtype"] = generate_subtype(card["Type"])
        if card["Name"] == "":
            if "Location" in card["Type"]:
                name = generate_location_name(
                    card["Green"],
                    card["Blue"],
                    card["Red"],
                    card["Colorless"],
                    card["Effect"],
                    names,
                )
            elif card["Type"] == "Hero":
                name = generate_hero_name(
                    names,
                )
            elif card["Type"] == "Equipment":
                name = generate_equipment_name(
                    card["Green"],
                    card["Blue"],
                    card["Red"],
                    card["Colorless"],
                    card["Effect"],
                    names,
                )
            elif "#" in card["Power"]:
                name1, name2 = generate_transform_name(
                    card["Subtype"],
                    card["Green"],
                    card["Blue"],
                    card["Red"],
                    card["Colorless"],
                    card["Power"],
                    card["Effect"],
                    names,
                )
                name = f"{name1}#{name2}"
            else:
                name = generate_creature_name(
                    card["Subtype"],
                    card["Green"],
                    card["Blue"],
                    card["Red"],
                    card["Colorless"],
                    card["Power"],
                    card["Effect"],
                    names,
                )
            card["Name"] = name
            names.add(name)
        if card["Subtype"] == "":
            if "#" in card["Power"]:
                name1, name2 = card["Name"].split("#")
                subtype1 = generate_subtype_from_name(card["Type"], name1)
                subtype2 = generate_subtype_from_name(card["Type"], name2)
                card["Subtype"] = f"{subtype1}#{subtype2}"
            else:
                card["Subtype"] = generate_subtype_from_name(card["Type"], card["Name"])
        if card["Edition"] == "":
            card["Edition"] = edition
        new_id = generate_card_id(
            card["Red"],
            card["Green"],
            card["Blue"],
            card["Colorless"],
            card["Power"],
            card["Effect"],
        )
        if card["Edition"] != "":
            print(card["Edition"], card)
            text, version = card["Edition"].split(" ")
            major, minor, patch = map(int, version.split("."))
            if card["ID"] != "" and new_id != card["ID"]:
                card["Edition"] = f"Alpha {major}.{minor}.{patch+1}"
        else:
            card["Edition"] == edition
        if card["Artist"] == "":
            card["Artist"] = artist
        if card["Writer"] == "":
            card["Writer"] = writer
        new_card = {
            "Name": card["Name"],
            "Type": card["Type"],
            "Subtype": card["Subtype"],
            "Effect": card["Effect"],
            "Red": card["Red"],
            "Blue": card["Blue"],
            "Green": card["Green"],
            "Colorless": card["Colorless"],
            "Power": card["Power"],
            "Writer": card["Writer"],
            "Artist": card["Artist"],
            "Edition": card["Edition"],
            "ID": new_id,
        }
        all_cards.append(new_card)
        if print_all or card["ID"] != new_id:
            new_cards.append(new_card)

    if new_cards == []:
        print("No new cards")

    if not print_all:
        # Write a new csv
        write_csv(new_cards, output_path)
    else:
        write_csv(new_cards, output_path)

    # Write all changes into the old csv
    write_csv(all_cards, file_path)

    # Print stats about the cards
    plot_stats(all_cards, filename="all_cards")


def plot_stats(cards, filename, save_dir="stats"):
    if type(cards) == str:
        cards = read_csv(cards)
    plot_color_stats(cards, filename, save_dir)
    plot_mana_cost_distribution(cards, filename, save_dir)
    plot_effect_chart(cards, filename, save_dir)
    # TODO improve existing stats.
    # Add stats about effects. How many 'On Play', 'On Revive' effects etc.
    # Maybe even how many 'On Play' effects destroy a card etc.
