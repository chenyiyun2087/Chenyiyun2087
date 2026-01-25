# master_script.py
import datetime
import os
import sys

# --- Import functions from your existing scripts ---
# It's crucial that InvestingProExcelPreprocess.py and InvestingProExcelParser.py
# are in the same directory as this master script, or in Python's path.
try:
    from InvestingProExcelPreprocess import process_excel_file

    print("INFO: Successfully imported 'process_excel_file' from InvestingProExcelPreprocess.py")
except ImportError:
    print("ERROR: Could not import 'process_excel_file' from InvestingProExcelPreprocess.py.")
    print(
        "Please ensure 'InvestingProExcelPreprocess.py' is in the same directory as this script and contains the function.")
    sys.exit(1)  # Exit if critical component is missing

try:
    from InvestingProExcelParser import filter_file_data

    print("INFO: Successfully imported 'filter_file_data' from InvestingProExcelParser.py")
except ImportError:
    print("ERROR: Could not import 'filter_file_data' from InvestingProExcelParser.py.")
    print(
        "Please ensure 'InvestingProExcelParser.py' is in the same directory as this script and contains the function.")
    sys.exit(1)  # Exit if critical component is missing


def run_daily_processing_pipeline():
    """
    Orchestrates the daily Excel/CSV processing pipeline.
    """
    # 1. Get today's date and format it
    today = datetime.date.today()
    date_str = today.strftime("%Y-%m-%d")  # Format: YYYY-MM-DD

    # 2. Define filenames based on today's date
    base_name_prefix = "cyy - cyy - "
    base_dir  = '/Users/chenyiyun/Trade/InvestingPro/'
    initial_input_xlsx = base_dir +f"{base_name_prefix}{date_str}.xlsx"
    intermediate_csv = f"{base_name_prefix}{date_str}.csv"
    # Final output name based on discussion (e.g., YYYY-MM-DD-filtered.csv)
    # If you strictly need "cyy - cyy - filterYYYY-MM-DD.csv", change to:
    # final_output_csv = f"{base_name_prefix}filter{date_str}.csv"
    final_output_csv =base_dir + f"{base_name_prefix}{date_str}-filtered.csv"

    print(f"\n--- Orchestration Started for Date: {date_str} ---")
    print(f"Initial Input Excel: {initial_input_xlsx}")
    print(f"Intermediate CSV:    {intermediate_csv}")
    print(f"Final Filtered CSV:  {final_output_csv}")

    # --- Step 1: Preprocessing (Excel to CSV) ---
    print(f"\n[STEP 1] Running Preprocessing...")
    print(f"Input:  '{initial_input_xlsx}'")
    print(f"Output: '{intermediate_csv}'")

    # Check if initial input file exists
    if not os.path.exists(initial_input_xlsx):
        print(
            f"ERROR: Initial input file '{initial_input_xlsx}' not found. Please create it or place it in the correct directory.")
        print("--- Orchestration Halted (Step 1 Error) ---")
        return

    try:
        process_excel_file(initial_input_xlsx, intermediate_csv)
        # The process_excel_file function should print its own success/failure messages.
        # We'll check if the output file was created as a basic success indicator.
        if os.path.exists(intermediate_csv):
            print(f"INFO: [STEP 1] Preprocessing seems to have completed. Output file '{intermediate_csv}' found.")
        else:
            print(
                f"ERROR: [STEP 1] Preprocessing completed, but output file '{intermediate_csv}' was NOT created. Please check logs from InvestingProExcelPreprocess.py.")
            print("--- Orchestration Halted (Step 1 Error) ---")
            return
    except Exception as e:
        print(f"ERROR: [STEP 1] An unexpected error occurred while calling 'process_excel_file': {e}")
        import traceback
        traceback.print_exc()
        print("--- Orchestration Halted (Step 1 Error) ---")
        return

    # --- Step 2: Parsing and Filtering (CSV to Filtered CSV) ---
    print(f"\n[STEP 2] Running Parsing & Filtering...")
    print(f"Input:  '{intermediate_csv}'")
    print(f"Output: '{final_output_csv}'")
    try:
        # The filter_file_data function should print its own success/failure messages
        # and ideally returns the filtered DataFrame or None on failure.
        filtered_data = filter_file_data(intermediate_csv, final_output_csv)

        if filtered_data is not None:  # Assuming filter_file_data returns None on major error, or the dataframe
            if os.path.exists(final_output_csv):
                # Check if the returned dataframe is empty if file exists, as an indicator
                if not filtered_data.empty:
                    print(
                        f"INFO: [STEP 2] Parsing & Filtering completed. Output file '{final_output_csv}' created/updated with {len(filtered_data)} rows.")
                else:
                    print(
                        f"INFO: [STEP 2] Parsing & Filtering completed. Output file '{final_output_csv}' created/updated, but it contains no data after filtering.")
            else:
                print(
                    f"ERROR: [STEP 2] filter_file_data ran, but final output file '{final_output_csv}' was NOT created. Please check logs from InvestingProExcelParser.py.")
                print("--- Orchestration Halted (Step 2 Error) ---")
                return
        else:
            print(
                f"ERROR: [STEP 2] 'filter_file_data' indicated an issue or returned no data. Output file '{final_output_csv}' might not be as expected or not created.")
            print("--- Orchestration Halted (Step 2 Error) ---")
            return

    except Exception as e:
        print(f"ERROR: [STEP 2] An unexpected error occurred while calling 'filter_file_data': {e}")
        import traceback
        traceback.print_exc()
        print("--- Orchestration Halted (Step 2 Error) ---")
        return

    print(f"\n--- Orchestration Finished Successfully for Date: {date_str} ---")


if __name__ == "__main__":
    run_daily_processing_pipeline()