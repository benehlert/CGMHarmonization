import os

# Define the files to combine in order
files_to_combine = [
    ('cgm_ingest.py', 'cgm_ingest.py'),
    ('first_loader.py', 'clean/debug/loaders/first_loader.py'),
    ('first_traceback.txt', 'clean/debug/loaders/first_traceback.txt'),
    ('second_loader.py', 'clean/debug/loaders/second_loader.py'),
    ('second_traceback.txt', 'clean/debug/loaders/second_traceback.txt'),
    ('manifest.json', 'clean/manifest.json')
]

# Create the output file
with open('everything.txt', 'w') as outfile:
    for filename, filepath in files_to_combine:
        # Write the filename header
        outfile.write(f'#[{filename}]\n\n')
        
        # Read and write the file contents
        with open(filepath, 'r') as infile:
            outfile.write(infile.read())
            outfile.write('\n\n')  # Add some spacing between files

print("Files have been combined into everything.txt") 