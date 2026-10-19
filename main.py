import os
import kaggle
from dotenv import load_dotenv
import csv
from datetime import datetime, date
import json

# this is an incremental project (as in im doing small tasks to not overwhelm myself with a side-project)

# ============================================ CONFIGS ============================================
config_file_path = "config.json"


def data_rework_needed():
    with open('datasets\\jobs.csv', mode='r', encoding='utf-8', newline='') as file:
        reader = csv.reader(file)
        
        # Optional: Skip the header row if your file has one
        header = next(reader) 
        counter = 0
        junior_pos = ["junior", "jr",]
        senior_pos = ["senior", "sr", "expert"]
        manager_pos = ["manager", "lead"]

        for row in reader:
            job_title = str(row[2]).lower()
            
            # skills are ";" separated
            # some skills are empty, those need to be cleaned
            skills_needed = (row[12])
            print(f"start->{skills_needed}<-end")

            is_junior = any(word in job_title for word in junior_pos)
            print(1 if is_junior else 0)

            counter += 1
            if counter >= 100: # to limit the prints
                break


def read_config_file(key="default"):
    with open (config_file_path, mode='r', encoding="utf-8") as file:
        config = json.load(file)
    if key == "default":
        return config
    else:
        if key in config.keys():
            return config[key]
        else:
            print("no such key exists in the config file")
            return False

def write_config_file(key, val):
    with open("config.json", "r", encoding="utf-8") as file:
        conf = json.load(file)

    conf[key] = val

    with open("config.json", "w", encoding="utf-8") as file:
        json.dump(conf, file, indent=4)
    
    return True


def check_db_update_date():
    last_date = read_config_file("DB_last_updated") # type: ignore
    
    if last_date == False:
        return True
    else:
        date_object = datetime.strptime(last_date, "%Y-%m-%d").date()
        today = date.today()
        delta = today - date_object
        if delta.days >= 30:
            return True
        else:
            return False


def download_datasets():
    load_dotenv()

    username = os.getenv('KAGGLE_USERNAME')
    key = os.getenv('KAGGLE_KEY')

    assert username is not None, "KAGGLE_USERNAME is missing from the .env file"
    assert key is not None, "KAGGLE_KEY is missing from the .env file"


    os.environ['KAGGLE_USERNAME'] = username
    os.environ['KAGGLE_KEY'] = key


    kaggle.api.authenticate()

    dataset_slug = 'chandranshbinjola/job-listings'
    download_path = './datasets'

    kaggle.api.dataset_download_files(dataset_slug, path=download_path, unzip=True)
    print(f"Dataset downloaded to {download_path}")


def driver():
    if check_db_update_date():
        download_datasets()
        write_config_file("DB_last_updated", str(date.today()))

    else:
        print(f"Dataset updated in the last 30 days.")
        print(f"No dataset update is made.")

if __name__ == "__main__":
    driver()