import shutil
import os
import csv
import pandas as pd


def delete_contents(directory):
    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)  # Remove file or symbolic link
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)  # Remove directory and all its contents
        except Exception as e:
            print(f"Failed to delete {file_path}. Reason: {e}")


def sort_cards(csv_file_path: str, sort_by: list):
    """Sorts cards in a csv file given a sorting criteria.

    csv_file_path (string): Path to the csv file.
    sort_by (List[string]): Give a list of sorting criteria with 'cost', 'Power', 'Name' and 'Color'.
    """
    # Read the CSV file
    with open(csv_file_path, mode="r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        data = list(reader)

    sort_by = [
        (["Colorless", "Green", "Red", "Blue"] if item == "Color" else item)
        for item in sort_by
    ]
    sort_by = [
        x
        for sublist in sort_by
        for x in (sublist if isinstance(sublist, list) else [sublist])
    ]
    # Validate sorting criteria
    if not all(c in reader.fieldnames for c in sort_by):
        raise ValueError(
            "One or more sorting criteria are not in the CSV file headers."
        )

    # Sort data based on the provided criteria
    data.sort(key=lambda row: tuple(row[c] for c in sort_by))

    # Write the sorted data back to the CSV file
    with open(csv_file_path, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerows(data)


def write_csv_by_effect(csv_file_path: str, output_dir: str, effects: list):
    """
        Reads all cards in csv file path and writes len(effects) new csv files with all cards containing that effect.

        Args:
            csv_file_path (String): Path csv file with all cards

        Usage:
            write_csv_by_effect(
                r"tables\all_cards.csv",
                r"tables\sorted_by_effect",
                [
                    "on play",
                    "draw",
                    "destroy",
                    "destruction",
                    "return",
                    "move",
                    "discard",
                    "revive",
                    "additional cost",
                    "while in your graveyard",
            ],
    )
    """
    with open(csv_file_path, encoding="utf-8") as file:
        reader = csv.DictReader(file)
        data = list(reader)

    output_files = {e: [] for e in effects}

    for row in data:
        for effect in effects:
            if effect in row["Effect"].lower():
                output_files[effect].append(row)

    for e in effects:
        with open(
            f"{os.path.join(output_dir, e)}.csv", mode="w", newline="", encoding="utf-8"
        ) as file:
            writer = csv.DictWriter(file, fieldnames=reader.fieldnames)
            writer.writeheader()
            writer.writerows(output_files[e])


def swap_rows(csv_file_path: str, col_a: str, col_b: str):
    """

    Usage:
        swap_rows(r"tables\creatures.csv", "Red", "Blue")
    """
    with open(csv_file_path, mode="r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        data = list(reader)

    for row in data:
        row[col_a], row[col_b] = row[col_b], row[col_a]

    with open(csv_file_path, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerows(data)


def prepend_to_column(csv_file_path, column, prepend_text):
    """
    Usage:
        prepend_to_name_column(r"tables\all_cards.csv", "Creature - ")
    """
    # Read the CSV file
    with open(csv_file_path, mode="r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        data = list(reader)  # Read data into a list
        fieldnames = reader.fieldnames  # Store field names

    # Modify the "Name" column
    for row in data:
        if "Name" in row:  # Ensure the column exists
            row[column] = prepend_text + row[column]

    # Write the updated data back to the file
    with open(csv_file_path, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)


def insert_new_column(
    csv_file_path: str,
    position: int,
    column_name: str = "NewColumn",
    default_value: str = "Creature",
):
    """
    Inserts a new column into a CSV file with a specified default value.

    Args:
        csv_file_path (string): Path to the CSV file.
        position (integer): The index at which the new column should be inserted (0-based).
        column_name (string): Name of the new column to be added (default is "NewColumn").
        default_value (string): Default value for the new column (default is 'Creature').
    """
    # Read the CSV file
    with open(csv_file_path, mode="r", newline="", encoding="utf-8") as file:
        reader = list(csv.reader(file))

    # Ensure the position is within valid range
    num_cols = len(reader[0])
    if position < 0 or position > num_cols:
        print(
            f"Error: Position {position} is out of range. The file has {num_cols} columns."
        )
        return

    # Insert the new column header at the specified position
    reader[0].insert(position, column_name)

    # Insert the default value at the specified position for each row
    for row in reader[1:]:
        row.insert(position, default_value)

    # Write back the modified data
    with open(csv_file_path, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerows(reader)

    print(f"Column '{column_name}' added successfully at position {position}.")


def write_default_value(csv_file_path: str, column: str, value: str, overwrite: bool):
    """ """
    # Read the CSV file
    with open(csv_file_path, mode="r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        data = list(reader)  # Read data into a list
        fieldnames = reader.fieldnames  # Store field names

    # Modify the "Name" column
    for row in data:
        if overwrite or row[column] == "":  # Ensure the column is empty
            row[column] = value

    # Write the updated data back to the file
    with open(csv_file_path, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)


def combine_decklists_to_csv(
    all_cards_csv: str,
    decklist_dir: str,
    output_csv_file_path: str,
    leftover_csv_file_path: str,
):
    """
    Reads all csv files in decklist_dir and combines them into csv file at output_csv_file_path
    Every card that is in all_cards_csv but not in output_csv_file_path gets written into leftover_csv_file_path
    """
    # Read the list of all cards
    with open(all_cards_csv, mode="r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        all_cards = list(reader)
        fieldnames = reader.fieldnames  # Store column headers

    # Combine all decklists
    combined_decklists = []
    for file in os.listdir(decklist_dir):
        if file.endswith(".csv"):
            file_path = os.path.join(decklist_dir, file)
            with open(file_path, mode="r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                combined_decklists.extend(reader)  # Append data

    # Write combined decklist to CSV
    with open(output_csv_file_path, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(combined_decklists)

    # Identify leftover cards
    combined_set = {row["Name"] for row in combined_decklists}
    leftover_cards = [row for row in all_cards if row["Name"] not in combined_set]

    # Write leftover cards to CSV
    with open(leftover_csv_file_path, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(leftover_cards)


# swap_rows(r"tables\creatures.csv", "Red", "Green")
# prepend_to_column(r"tables\creatures.csv", "Type", "Creature - ")
# sort_cards(r"tables\creatures.csv", sort_by=["Color", "Power", "Name"])
# write_csv_by_effect(
#     r"tables\creatures.csv",
#     "tables\\sorted_by_effect",
#     [
#         "on play",
#         "draw",
#         "destroy",
#         "destruction",
#         "return",
#         "move",
#         "discard",
#         "revive",
#         "additional cost",
#         "while in your graveyard",
#     ],
# )
# insert_new_column(r"tables\creatures.csv", position = 9, column_name = "Writer", default_value="Llama3.2:3b")
# write_default_value(r"tables\creatures.csv", column = "Artist", value = "Sana:0.6b", overwrite = True)
combine_decklists_to_csv(r"tables\all_cards.csv", "decklists", "tables\decklists.csv", "tables\leftover.csv")
