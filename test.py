import csv

# file_path = r"decklists\arcane_arsenal.csv"
file_path = r"decklists\\unstoppable_growth.csv"
data = []

# Open and read the CSV file
with open(file_path, mode='r', encoding='utf-8') as file:
    reader = csv.DictReader(file)
    
    for row in reader:
        print(row['Name'])
