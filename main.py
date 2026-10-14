import os
import kaggle
from dotenv import load_dotenv

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

