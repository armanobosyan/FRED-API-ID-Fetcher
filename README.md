# FRED API ID Data Fetcher
This Python script is designed to interact with the Federal Reserve Economic Data (FRED) API to fetch, save, and manage economic data categories. Here's a breakdown of its functionality:

## Libraries Used
requests: For making HTTP requests to the FRED API.
pandas: For data manipulation and saving the fetched data as CSV files.
ratelimit: To apply rate limiting on API requests, preventing overuse.
tqdm: Provides a progress bar for loops, enhancing user experience during data fetching.
os: For file and directory operations, ensuring compatibility across operating systems.

## Key Components
FRED API Key and Save Path
FRED_KEY: Placeholder for the user's FRED API key, necessary for authentication.
SAVE_PATH: The directory path where fetched category data will be saved, dynamically set to a folder named saved_categories in the current working directory.

## Functions
api_url(category_id, api_key=None): Constructs the URL for API requests based on the category ID and optionally provided API key.

import_category_children(category_id, api_key=None): Fetches data for child categories of a given category ID from the FRED API. It uses rate limiting to ensure compliance with API usage policies. The data is returned as a pandas DataFrame, with any 'notes' column removed for cleanliness.

save_df_to_file(df, filename): Saves a given DataFrame to a CSV file within the SAVE_PATH directory, creating the directory if it doesn't exist.

read_df_from_file(filename): Attempts to read a DataFrame from a specified CSV file within the SAVE_PATH directory, facilitating data persistence and resume capability.

fetch_and_save_categories(needed_categories): Orchestrates the fetching process for categories, leveraging the tqdm library for progress visualization. It checks for existing data to resume where left off, fetches data for necessary categories, and saves the results incrementally. This function iterates through a predefined depth (10 levels deep in this case) or until no more categories are needed to be fetched.

## Execution Flow
Initialization: Sets up the FRED API key and the save path.
Data Fetching: Begins fetching category data starting with the root category (ID 0). It utilizes a loop to fetch data for each level of categories, checking for existing files to resume from if interrupted.
Saving and Resuming: After fetching, data is saved to CSV files, allowing the process to be paused and resumed without loss of progress.
Completion: Once all categories have been fetched and saved, the script prints a completion message.

## Use Case
This script is particularly useful for users needing comprehensive economic data from FRED for analysis, research, or educational purposes. It automates the process of data collection, respects API rate limits, and provides a user-friendly way to manage large datasets.

## Features
- Fetches data from the FRED API.
- Rate limiting to comply with API constraints.
- Resume capability to pick up where left off.
- Saves data in CSV format.

# Getting Started

About the Federal Reserve Economic Data (FRED) API, offered by the Federal Reserve Bank of St. Louis, is a comprehensive source of economic data. It provides free access to over 765,000 time series from various national and international sources, covering a wide array of economic indicators such as GDP, inflation rates, employment figures, and more. Here's a succinct overview:

### Key Features
Extensive Data Collection: Access a vast array of U.S. and international economic data.
Regular Updates: Data series are updated regularly, ensuring access to the latest information.
Flexible Formats: Supports JSON, XML, and plain text formats for easy integration.
Free Access: Available at no cost, requiring an API key for usage.
### Usage
Economic Analysis: Ideal for researchers, analysts, and students for economic studies and forecasting.
Financial Applications: Used by financial professionals for market analysis and investment planning.
Data Visualization: Enables journalists and bloggers to create compelling economic visualizations.
### API Key Getting Started
API Key: Register on the FRED website to obtain an API key.
Documentation: Review the API guide for detailed usage instructions.
Query Data: Use the API to request specific data series with your API key.

# Conclusion
The FRED API is a powerful tool for accessing a wide range of economic data, supporting various research, analysis, and educational needs.
For detailed information, visit the FRED API documentation.

# HYSTORY: This Python is fork of orignal based posted by joshuaulrich/freddy-mcfredface 
This repository and its contents are inspired by the work of JD Long and further developed by Eric Bickel. Originally, Eric aimed to download CSV files containing series descriptions provided by FRED. However, since May 2000, FRED has ceased the distribution of these CSV files directly.

JD Long crafted a script capable of navigating through FRED's extensive network of parent/child categories to compile a comprehensive list of all FRED series available. This script was notably reliant on the fredr package, a tool that has been archived on CRAN in the time since. In continuation of JD's effort, I have adapted his script into Python, eliminating the dependency on fredr and addressing the challenge posed by FRED's discontinuation of direct CSV distribution.

The Python script is designed to fetch and save category data from the FRED API, incorporating features such as built-in rate limiting and the ability to resume interrupted sessions. This ensures efficient and reliable data fetching without exceeding API limits, making extensive data collection sessions manageable.


