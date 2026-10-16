import os
import kaggle
from dotenv import load_dotenv
import csv

# this is an incremental project (as in im doing small tasks to not overwhelm myself with a side-project)

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

