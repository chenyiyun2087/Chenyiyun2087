import os
import pandas as pd


def _parse_bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    val_norm = str(val).strip().lower()
    if val_norm in {"1", "true", "yes", "y", "on"}:
        return True
    if val_norm in {"0", "false", "no", "n", "off"}:
        return False
    return default


def process_excel_file(input_excel_filename, output_csv_filename, *, skiprows: int = 7, delete_first_data_row: bool = True, drop_first_two_columns: bool = True):
    """
    Reads an Excel file, performs specified row and column deletions,
    and saves the result as a CSV file.

    Args:
    input_excel_filename (str): The name of the input .xlsx file.
    output_csv_filename (str): The name for the output .csv file.
    """
    try:
        # Step 1a: Read the Excel file.
        # skiprows=7 will skip the first 7 rows of the Excel file.
        # The 8th row (0-indexed as 7) will be used as the header.
        print(f"INFO: Reading Excel file: '{input_excel_filename}'...")
        # Allow overriding via function params or environment variables
        env_skiprows = os.getenv('IP_SKIPROWS')
        if env_skiprows is not None and str(env_skiprows).isdigit():
            skiprows = int(env_skiprows)
        delete_first_data_row = _parse_bool_env('IP_DELETE_FIRST_DATA_ROW', delete_first_data_row)
        drop_first_two_columns = _parse_bool_env('IP_DROP_FIRST_TWO_COLUMNS', drop_first_two_columns)

        print(f"INFO: skiprows={skiprows}. The next row becomes the header.")
        df = pd.read_excel(input_excel_filename, skiprows=skiprows, engine='openpyxl')
        print("INFO: Excel file read successfully.")
        print(f"INFO: DataFrame shape after initial load (and skipping rows 1-7): {df.shape}")
        # Debug trace: locate specific name if requested
        trace_name = os.getenv('IP_TRACE_NAME')
        if trace_name:
            try:
                hits = df.astype(str).apply(lambda s: s.str.contains(trace_name, na=False))
                rows = df[hits.any(axis=1)]
                print(f"TRACE[{trace_name}] after_read: found {len(rows)} rows")
                if not rows.empty:
                    print(rows.head(3))
            except Exception as _:
                pass

        if df.empty:
            print(
                "WARNING: DataFrame is empty after skipping initial rows and reading header. Check if original row 8 and beyond contain data.")
        else:
            print("INFO: Preview of data (first 3 rows with new header):")
            print(df.head(3))

        # Step 1b: Optionally delete the first data row (the row immediately after header)
        if delete_first_data_row:
            if not df.empty:
                if 0 in df.index:
                    print("INFO: Deleting first data row at index 0 as configured...")
                    df = df.drop(index=0)
                    df = df.reset_index(drop=True)
                    print("INFO: First data row deleted.")
                    print(f"INFO: DataFrame shape after deletion: {df.shape}")
                    if not df.empty:
                        print("INFO: Preview after deleting first data row (first 3 rows):")
                        print(df.head(3))
                    else:
                        print("WARNING: DataFrame is empty after deleting first data row.")
                else:
                    print("WARNING: No data row at index 0 to delete. Skipping.")
            else:
                print("INFO: DataFrame is empty. Skipping first data row deletion.")
        else:
            print("INFO: Configured to keep the first data row (no deletion).")

        # Debug trace after first-row deletion
        if trace_name:
            try:
                hits = df.astype(str).apply(lambda s: s.str.contains(trace_name, na=False))
                rows = df[hits.any(axis=1)]
                print(f"TRACE[{trace_name}] after_drop_first_row: found {len(rows)} rows")
                if not rows.empty:
                    print(rows.head(3))
            except Exception as _:
                pass

        # Step 2: Optionally delete columns A and B (the first two columns)
        if drop_first_two_columns:
            if not df.empty:
                if df.shape[1] >= 2:
                    print("INFO: Deleting the first two columns (A and B) as configured...")
                    columns_to_drop = df.columns[[0, 1]]
                    df = df.drop(columns=columns_to_drop)
                    print("INFO: First two columns deleted.")
                    print(f"INFO: DataFrame shape after deleting columns: {df.shape}")
                    if not df.empty:
                        print("INFO: Preview after deleting columns (first 3 rows):")
                        print(df.head(3))
                    else:
                        print("WARNING: DataFrame is empty after deleting columns.")
                elif df.shape[1] == 1:
                    print("INFO: DataFrame has only one column. Deleting it as configured.")
                    df = df.drop(columns=df.columns[0])
                    print("INFO: Single column deleted.")
                    print(f"INFO: DataFrame shape after deleting the only column: {df.shape}")
                else:
                    print("INFO: DataFrame has no columns to delete.")
            else:
                print("INFO: DataFrame was empty before attempting to delete columns, skipping this step.")
        else:
            print("INFO: Configured to keep the first two columns (no column deletion).")

        # Debug trace after column deletion
        if trace_name:
            try:
                hits = df.astype(str).apply(lambda s: s.str.contains(trace_name, na=False))
                rows = df[hits.any(axis=1)]
                print(f"TRACE[{trace_name}] after_drop_cols: found {len(rows)} rows")
                if not rows.empty:
                    print(rows.head(3))
            except Exception as _:
                pass

        # Step 3: Save the processed DataFrame to a CSV file.
        print(f"INFO: Saving processed data to '{output_csv_filename}'...")
        df.to_csv(output_csv_filename, index=False, encoding='utf-8-sig')
        print(f"SUCCESS: Processed data saved successfully to '{output_csv_filename}'.")

    except FileNotFoundError:
        print(
            f"ERROR: Input Excel file '{input_excel_filename}' not found. Please ensure the file name and path are correct.")
    except pd.errors.EmptyDataError:
        print(
            f"ERROR: The Excel file '{input_excel_filename}' is empty or no data found after the specified header row.")
    except IndexError as e:
        print(
            f"ERROR: An IndexError occurred, possibly due to insufficient rows/columns for the operations. Details: {e}")
    except Exception as e:
        print(f"ERROR: An unexpected error occurred: {e}")
        import traceback
        print(traceback.format_exc())


if __name__ == '__main__':
    # --- Configuration ---
    # Replace this with the actual name of your Excel file
    excel_file_to_process = "cyy - cyy - 2025-05-28.xlsx"

    # This will be the name of the output CSV file
    output_csv_file_name = "cyy - cyy - 2025-05-28.csv"

    print("--- Starting Excel File Processing ---")
    process_excel_file(excel_file_to_process, output_csv_file_name)
    print("--- Processing Finished ---")