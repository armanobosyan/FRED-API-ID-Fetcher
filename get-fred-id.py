# FRED API ID Data Fetcher
import requests
import pandas as pd
from ratelimit import limits, sleep_and_retry
from tqdm import tqdm
import os

# Set your FRED API key here. This key is required to authenticate and make requests to the FRED API.
FRED_KEY = 'you_key'

# Directory to save fetched categories, relative to the script's current working directory.
# This path is where the script will save the CSV files containing the fetched data.
SAVE_PATH = os.path.join(os.getcwd(), 'saved_categories')

def api_url(category_id, api_key=None):
    """
    Constructs the API URL for a given category ID.
    
    Parameters:
    - category_id: The ID of the category for which to fetch child categories.
    - api_key: Optional; the FRED API key. If not provided, FRED_KEY is used.
    
    Returns:
    - A string containing the full URL for the API request.
    """
    if api_key is None:
        api_key = FRED_KEY
    url = f"https://api.stlouisfed.org/fred/category/children?category_id={category_id}&file_type=json&api_key={api_key}"
    return url

@sleep_and_retry
@limits(calls=1, period=2)
def import_category_children(category_id, api_key=None):
    """
    Fetches category children from the FRED API with rate limiting.
    
    Parameters:
    - category_id: The ID of the category to fetch its children.
    - api_key: Optional; the FRED API key. If not provided, FRED_KEY is used.
    
    Returns:
    - A pandas DataFrame containing the fetched category data.
    """
    url = api_url(category_id, api_key)
    response = requests.get(url)
    data = response.json().get('categories', [])
    df = pd.DataFrame(data)
    if 'notes' in df.columns:
        df.drop(columns=['notes'], inplace=True)
    return df

def save_df_to_file(df, filename):
    """
    Saves DataFrame to a CSV file in the specified SAVE_PATH directory.
    
    Parameters:
    - df: The pandas DataFrame to save.
    - filename: The name of the file to save the DataFrame to.
    """
    if not os.path.exists(SAVE_PATH):
        os.makedirs(SAVE_PATH)
    filepath = os.path.join(SAVE_PATH, filename)
    df.to_csv(filepath, index=False)

def read_df_from_file(filename):
    """
    Reads DataFrame from a CSV file if it exists.
    
    Parameters:
    - filename: The name of the file to read the DataFrame from.
    
    Returns:
    - A pandas DataFrame read from the file, or None if the file does not exist.
    """
    filepath = os.path.join(SAVE_PATH, filename)
    if os.path.exists(filepath):
        return pd.read_csv(filepath)
    return None

def fetch_and_save_categories(needed_categories):
    """
    Fetches categories, saves them, and checks for existing files to resume.
    
    Parameters:
    - needed_categories: A list of category IDs to fetch data for.
    
    Returns:
    - A pandas DataFrame containing all fetched categories.
    """
    all_categories = pd.DataFrame()
    for level in tqdm(range(10), desc="Fetching levels"):  # Assuming 10 levels deep max
        filename = f"fetched_level_{level}.csv"
        current_df = read_df_from_file(filename)
        if current_df is not None:
            print(f"Found existing data for level {level}, resuming...")
        else:
            temp_dfs = []
            for cat in tqdm(needed_categories, leave=False, desc="Categories"):
                df = import_category_children(cat)
                if df.empty or 'id' not in df.columns:
                    print(f"No data or 'id' column missing for category {cat}. Skipping.")
                    continue
                temp_dfs.append(df)
            if temp_dfs:
                current_df = pd.concat(temp_dfs, ignore_index=True)
                save_df_to_file(current_df, filename)
            else:
                print("No more categories to fetch. Ending.")
                break
        all_categories = pd.concat([all_categories, current_df], ignore_index=True)
        needed_categories = current_df['id'].unique().tolist()
    return all_categories

print("Starting data fetch...")
needed_categories = [0]  # Begin with the root category
fetched_categories = fetch_and_save_categories(needed_categories)

print("Data fetching completed.")

# End of code # FRED API ID Data Fetcher #
