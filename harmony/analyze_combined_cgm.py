#!/usr/bin/env python3
"""
Script to analyze combined_cgm.csv files in processed_data_new_data_combo directory.

This script:
1. Searches for all combined_cgm.csv files in the directory tree
2. Loads each file and removes duplicates based on (Timestamp, Glucose, Subject_ID)
3. Records summary statistics for each file
4. Outputs results to a CSV file

Memory efficient - processes one file at a time to avoid loading all data into memory.
"""

import os
import pandas as pd
import glob
from pathlib import Path
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def find_combined_cgm_files(base_dir):
    """
    Find all combined_cgm.csv files in the directory tree.
    
    Args:
        base_dir (str): Base directory to search
        
    Returns:
        list: List of file paths
    """
    pattern = os.path.join(base_dir, "**", "combined_cgm.csv")
    files = glob.glob(pattern, recursive=True)
    logger.info(f"Found {len(files)} combined_cgm.csv files")
    return files

def analyze_combined_cgm_file(file_path):
    """
    Analyze a single combined_cgm.csv file.
    
    Args:
        file_path (str): Path to the combined_cgm.csv file
        
    Returns:
        dict: Summary statistics
    """
    logger.info(f"Processing: {file_path}")
    
    try:
        # Get the parent directory name (immediately above the file)
        parent_dir = os.path.basename(os.path.dirname(file_path))
        
        # Read the file in chunks to handle large files
        chunk_size = 10000
        chunks = []
        
        # Read file in chunks
        for chunk in pd.read_csv(file_path, chunksize=chunk_size):
            chunks.append(chunk)
        
        # Combine chunks
        df = pd.concat(chunks, ignore_index=True)
        
        logger.info(f"Loaded {len(df)} rows from {file_path}")
        
        # Check if required columns exist
        required_cols = ['Timestamp', 'Glucose', 'Subject_ID']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            logger.warning(f"Missing columns {missing_cols} in {file_path}")
            return None
        
        # Remove duplicates based on (Timestamp, Glucose, Subject_ID)
        initial_rows = len(df)
        df_deduped = df.drop_duplicates(subset=['Timestamp', 'Glucose', 'Subject_ID'])
        final_rows = len(df_deduped)
        duplicates_removed = initial_rows - final_rows
        
        logger.info(f"Removed {duplicates_removed} duplicates from {file_path}")
        
        # Calculate summary statistics
        unique_subject_ids = df_deduped['Subject_ID'].nunique()
        total_rows = len(df_deduped)
        
        # Get unique source files
        if 'Source_File' in df_deduped.columns:
            unique_source_files = df_deduped['Source_File'].nunique()
            unique_source_file_names = df_deduped['Source_File'].unique().tolist()
        else:
            unique_source_files = 0
            unique_source_file_names = []
        
        # Create relative file path from harmony directory
        # Get the current working directory (should be harmony)
        harmony_dir = os.getcwd()
        try:
            relative_file_path = os.path.relpath(file_path, harmony_dir)
        except ValueError:
            # If file_path is not under harmony_dir, use the original path
            relative_file_path = file_path
        
        # Create summary
        summary = {
            'parent_directory': parent_dir,
            'file_path': relative_file_path,
            'unique_subject_ids': unique_subject_ids,
            'total_rows': total_rows,
            'unique_source_files': unique_source_files,
            'unique_source_file_names': unique_source_file_names,
            'duplicates_removed': duplicates_removed,
            'initial_rows': initial_rows
        }
        
        logger.info(f"Summary for {parent_dir}: {unique_subject_ids} subjects, {total_rows} rows, {unique_source_files} source files")
        
        return summary
        
    except Exception as e:
        logger.error(f"Error processing {file_path}: {str(e)}")
        return None

def main():
    """Main function to process all combined_cgm files."""
    
    # Set the base directory (relative to harmony directory)
    base_dir = "processed_data_testing_gpt5_old"
    
    if not os.path.exists(base_dir):
        logger.error(f"Base directory does not exist: {base_dir}")
        return
    
    # Find all combined_cgm files
    files = find_combined_cgm_files(base_dir)
    
    if not files:
        logger.warning("No combined_cgm.csv files found")
        return
    
    # Process each file
    results = []
    for file_path in files:
        summary = analyze_combined_cgm_file(file_path)
        if summary:
            results.append(summary)
    
    # Create results DataFrame
    if results:
        df_results = pd.DataFrame(results)
        
        # Save results to CSV
        output_file = "combined_cgm_analysis_results_test.csv"
        df_results.to_csv(output_file, index=False)
        
        logger.info(f"Results saved to: {output_file}")
        
        # Print summary
        print("\n" + "="*80)
        print("COMBINED CGM ANALYSIS RESULTS")
        print("="*80)
        print(f"Total files processed: {len(results)}")
        print(f"Total unique subjects across all files: {df_results['unique_subject_ids'].sum()}")
        print(f"Total rows across all files: {df_results['total_rows'].sum()}")
        print(f"Total duplicates removed: {df_results['duplicates_removed'].sum()}")
        print("\nDetailed results:")
        print(df_results[['parent_directory', 'unique_subject_ids', 'total_rows', 'unique_source_files', 'duplicates_removed']].to_string(index=False))
        
        # Show unique source file names for each dataset
        print("\n" + "="*80)
        print("UNIQUE SOURCE FILE NAMES BY DATASET")
        print("="*80)
        for _, row in df_results.iterrows():
            print(f"\n{row['parent_directory']}:")
            for source_file in row['unique_source_file_names']:
                print(f"  - {source_file}")
    
    else:
        logger.warning("No files were successfully processed")

if __name__ == "__main__":
    main()
