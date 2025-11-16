import requests
import time
import os
import hashlib

# Thee server is already running, so let's grab some of the env vars:
# Use absolute import since the script is run from the root directory
import env as SERVER_ENV
from backend import _sanitize_filename # Moved import to top level

# Now let's test the server:
port = SERVER_ENV.FLASK_PORT
server_url = f"http://localhost:{port}"
book_title = "077484a10743e5dd5d151013e8c732f4" # "Moby Dick"
# Directory where downloads should appear
download_dir = SERVER_ENV.INGEST_DIR
# Timeout for waiting for download
download_timeout_seconds = 60 * 5
# Polling interval
poll_interval_seconds = 5

# Helper function to check download status
def check_download_status(book_id):
    print(f"Polling status for {book_id}...")
    start_time = time.time()
    while time.time() - start_time < download_timeout_seconds:
        try:
            status_response = requests.get(f"{server_url}/api/status")
            status_response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
            status_data = status_response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching status: {e}. Retrying...")
            time.sleep(poll_interval_seconds)
            continue
        except ValueError: # Includes JSONDecodeError
            print(f"Error decoding status JSON. Response text: {status_response.text}. Retrying...")
            time.sleep(poll_interval_seconds)
            continue

        # Check success conditions based on download_path
        for status_key in ["available", "done"]:
            if status_key in status_data and book_id in status_data[status_key]:
                book_status_info = status_data[status_key].get(book_id)
                # Check if the status info is a dictionary and has a non-empty download_path
                if isinstance(book_status_info, dict) and book_status_info.get('download_path'):
                    print(f"Book {book_id} has download path '{book_status_info['download_path']}' in status '{status_key}'.")
                    return True, book_status_info

        # Check for error status
        if "error" in status_data and book_id in status_data["error"]:
            book_error_info = status_data["error"].get(book_id, "Unknown error")
            print(f"Book {book_id} failed with error: {book_error_info}")
            return False, book_error_info

        #print(f"Polling status for {book_id}... Status: {status_data}")
        time.sleep(poll_interval_seconds)

    print(f"Timeout waiting for book {book_id} download path to appear.")
    return False, None

# --- Test Execution ---
print("--- Starting E2E Test ---")

# Step 1 : Search for a book
print(f"Step 1: Searching for book '{book_title}' (moby dick)...")
search_params = {'query': book_title}
search_response = requests.get(f"{server_url}/api/search", params=search_params)
search_response.raise_for_status()
search_results = search_response.json()

assert isinstance(search_results, list), f"Expected search results to be a list, got {type(search_results)}"
assert len(search_results) > 0, f"No books found for query: {book_title}"
print(f"Found {len(search_results)} potential matches.")

# Assume the first result is the one we want
book_to_test = search_results[0]
book_id = book_to_test.get('id')
assert book_id, "First search result is missing an 'id'"
print(f"Selected book ID for testing: {book_id}")

# Step 2 : Get book details
print(f"Step 2: Getting details for book ID: {book_id}...")
info_params = {'id': book_id}
info_response = requests.get(f"{server_url}/api/info", params=info_params)
info_response.raise_for_status()
book_details = info_response.json()

assert isinstance(book_details, dict), f"Expected book details to be a dict, got {type(book_details)}"
assert book_details.get('id') == book_id, "Book details ID mismatch"
print(f"Successfully retrieved details for '{book_details.get('title', 'N/A')}'")

# Step 3 : Queue the book for download
print(f"Step 3: Queuing download for book ID: {book_id}...")
download_params = {'id': book_id}
download_response = requests.get(f"{server_url}/api/download", params=download_params)
download_response.raise_for_status()
download_status = download_response.json()

assert download_status.get('status') == 'queued', f"Expected status 'queued', got {download_status}"
print(f"Book {book_id} successfully queued for download.")

# Step 4 : Check the download status until available or timeout
print(f"Step 4: Checking download status for book ID: {book_id} (timeout: {download_timeout_seconds}s)...")
is_available, final_status = check_download_status(book_id)

assert is_available, f"Book download failed or timed out. Final status check: {final_status}"
print(f"Book {book_id} download confirmed as available.")

# Step 5 : Verify the file exists locally (optional but good)
print(f"Step 5: Verifying downloaded file exists...")
# Depend if env.USE_TITLE is true or false, the filename will be different
if SERVER_ENV.USE_BOOK_TITLE:
    # Ensure book_details is available; might need adjustment if Step 2 failed
    # Assuming book_details was successfully fetched in Step 2
    title_to_sanitize = book_details.get('title', book_title) # Use fetched title if available
    expected_filename = _sanitize_filename(title_to_sanitize) + ".epub" # Add extension
else:
    expected_filename = f"{book_id}.epub"

expected_filepath = os.path.join(download_dir, expected_filename)

assert os.path.exists(expected_filepath), f"Expected downloaded file not found at: {expected_filepath}"
print(f"Verified file exists: {expected_filepath}")

# Step 6 : Download the book
print(f"Step 6: Downloading book {book_id}...")
download_response = requests.get(f"{server_url}/api/localdownload?id={book_id}")
download_response.raise_for_status()
# Write book to temp file :
temp_file_path = os.path.join("/tmp", f"{book_id}.epub")
with open(temp_file_path, 'wb') as f:
    f.write(download_response.content)

# Compare the downloaded file to the expected file
# compare shasum of the two files
expected_sha256 = hashlib.sha256(open(expected_filepath, 'rb').read()).hexdigest()
downloaded_sha256 = hashlib.sha256(open(temp_file_path, 'rb').read()).hexdigest()
assert expected_sha256 == downloaded_sha256, f"Downloaded file SHA256 mismatch. Expected: {expected_sha256}, Got: {downloaded_sha256}"
print(f"Downloaded file SHA256 matches expected: {expected_sha256}")

print("--- E2E Test Completed Successfully ---")
