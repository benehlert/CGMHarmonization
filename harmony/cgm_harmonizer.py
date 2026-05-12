import os
import time
import argparse
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

from model_registry import DEFAULT_CGM_MODEL, resolve_model_name

# I need to consider whether I should use this batch process
# or whether I should just upload 1 file and then have the code interpreter
# process that file and give me code to process that file. Then I
# could run that code locally on all of the files.
# I should use smaller test files rather than all 200 or so files.
# I think using the running local python code will result in more
# reliable results.

# Load environment variables from .env file
load_dotenv()

def process_batch(client, assistant, file_ids, batch_num, total_batches):
    """Process a batch of files with the OpenAI assistant."""
    try:
        # Create a thread
        thread = client.beta.threads.create()
        print(f"Thread created for batch {batch_num}. ID: {thread.id}")
        
        # Create a message with the files attached
        message = client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=(
                f"Please identify which of the attached files contain CGM data (batch {batch_num}/{total_batches}). "
                "Merge all CGM data into a single CSV with standardized columns: "
                "[datetime_utc, subject, glucose]. If no subject ID is found, set subject='unknown'. "
                "Remove duplicates, sort by datetime, and produce the final file as 'harmonized_cgm_data_batch_{batch_num}.csv'. "
                "Feel free to use Python code in Code Interpreter."
            ),
            attachments=[
                {"file_id": file_id, "tools": [{"type": "code_interpreter"}]}
                for file_id in file_ids
            ]
        )
        
        # Start the run
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=assistant.id
        )
        print(f"Created run for batch {batch_num}. ID: {run.id}")
        
        # Poll for the run to finish
        while True:
            try:
                run = client.beta.threads.runs.retrieve(
                    thread_id=thread.id,
                    run_id=run.id
                )
                status = run.status
                print(f"Current run status for batch {batch_num}: {status}")
                if status in ["completed", "failed", "cancelled"]:
                    break
                time.sleep(3)
            except Exception as e:
                print(f"Error checking run status for batch {batch_num}: {e}")
                return

        if run.status != "completed":
            print(f"Run ended with status: {run.status}")
            return

        # Get final messages and look for file attachments
        try:
            messages = client.beta.threads.messages.list(thread_id=thread.id)
            for msg in messages:
                if msg.role == "assistant":
                    print(f"\n--- Assistant Message for batch {batch_num} ---")
                    print(msg.content)
                    # If there are file attachments, we can download them
                    for attachment in msg.attachments:
                        if attachment.file_id:
                            try:
                                file_content = client.files.content(attachment.file_id)
                                out_name = f"harmonized_cgm_data_batch_{batch_num}.csv"
                                with open(out_name, "wb") as f:
                                    f.write(file_content.read())
                                print(f"Downloaded file -> {out_name}")
                            except Exception as e:
                                print(f"Error downloading file {attachment.file_id}: {e}")
        except Exception as e:
            print(f"Error retrieving messages for batch {batch_num}: {e}")
            
    except Exception as e:
        print(f"Error processing batch {batch_num}: {e}")
        return

def main():
    parser = argparse.ArgumentParser(description="Legacy CGM harmonizer using the Assistants API.")
    parser.add_argument("--data-folder", required=True, help="Directory containing raw CSV files to harmonize.")
    parser.add_argument("--model", default=DEFAULT_CGM_MODEL)
    args = parser.parse_args()

    ########################################################################
    # 1. Setup your OpenAI API key
    ########################################################################
    client = OpenAI()  # Will automatically use OPENAI_API_KEY from environment

    ########################################################################
    # 2. Define the data files you want to process (local paths)
    ########################################################################
    data_folder = Path(args.data_folder)
    file_paths = []
    for root, dirs, files in os.walk(data_folder):
        for fname in files:
            if fname.endswith(".csv"):  # Only process CSV files
                file_paths.append(os.path.join(root, fname))

    # Make sure these paths exist and are the CGM or suspected CGM files
    print("Found files:", len(file_paths))

    ########################################################################
    # 3. Upload files to OpenAI in batches
    ########################################################################
    file_ids = []
    for path in file_paths:
        try:
            with open(path, "rb") as file:
                file_obj = client.files.create(
                    file=file,
                    purpose="assistants"
                )
                print(f"Uploaded {path} -> file_id: {file_obj.id}")
                file_ids.append(file_obj.id)
        except Exception as e:
            print(f"Failed to upload {path}: {e}")

    if not file_ids:
        print("No files uploaded successfully. Exiting.")
        return

    ########################################################################
    # 4. Create the assistant once
    ########################################################################
    try:
        assistant = client.beta.assistants.create(
            name="CGM Data Harmonizer",
            instructions=(
                "You are an assistant specialized in identifying Continuous Glucose Monitoring (CGM) data files, "
                "extracting them, and creating a single merged, cleaned CSV. "
                "When the user requests, use Python code in the Code Interpreter to read all attached files, "
                "identify CGM-like data, parse it to a consistent schema with columns (datetime, subject, glucose), "
                "and then output a single CSV containing the merged results."
            ),
            model=resolve_model_name(args.model),
            tools=[{"type": "code_interpreter"}]
        )
        print(f"Assistant created. ID: {assistant.id}")
    except Exception as e:
        print(f"Error creating assistant: {e}")
        return

    ########################################################################
    # 5. Process files in batches of 10
    ########################################################################
    batch_size = 10
    total_batches = (len(file_ids) + batch_size - 1) // batch_size
    
    for i in range(total_batches):
        start_idx = i * batch_size
        end_idx = min((i + 1) * batch_size, len(file_ids))
        batch_file_ids = file_ids[start_idx:end_idx]
        print(f"\nProcessing batch {i+1}/{total_batches} with {len(batch_file_ids)} files")
        process_batch(client, assistant, batch_file_ids, i+1, total_batches)

if __name__ == "__main__":
    main()
