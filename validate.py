import json
import os
import sys

def load_pli_files(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def check_files(pli_files, segments_dir):
    # Create a set of filenames from pli-files.json
    pli_filenames = {entry['filename'] for entry in pli_files}
    
    # Set to keep track of files in segments directory
    segment_files = set()
    
    # Check files in segments directory
    for filename in os.listdir(segments_dir):
        if filename.endswith('.tsv'):
            base_name = os.path.splitext(filename)[0]
            segment_files.add(base_name)
            if base_name not in pli_filenames:
                print(f"Error: No entry in pli-files.json for {filename}", file=sys.stderr)
    
    # Check for entries in pli-files.json that don't have corresponding files
    for filename in pli_filenames:
        if filename not in segment_files:
            print(f"Error: No file in segments/ for entry {filename}", file=sys.stderr)
    print("Validation complete")


def main():
    pli_files_path = 'pli-files.json'
    segments_dir = 'segments'
    
    # Load pli-files.json
    try:
        pli_files = load_pli_files(pli_files_path)
    except FileNotFoundError:
        print(f"Error: {pli_files_path} not found", file=sys.stderr)
        return
    except json.JSONDecodeError:
        print(f"Error: {pli_files_path} is not a valid JSON file", file=sys.stderr)
        return
    
    # Check files
    if not os.path.isdir(segments_dir):
        print(f"Error: {segments_dir} directory not found", file=sys.stderr)
        return

    else:        
        check_files(pli_files, segments_dir)

if __name__ == '__main__':
    main()