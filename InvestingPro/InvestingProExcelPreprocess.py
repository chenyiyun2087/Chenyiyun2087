import pandas as pd


def process_excel_file(input_excel_filename, output_csv_filename):
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
        print(f"INFO: Original rows 1-7 will be skipped. Original row 8 will become the header.")
        df = pd.read_excel(input_excel_filename, skiprows=7, engine='openpyxl')
        print("INFO: Excel file read successfully.")
        print(f"INFO: DataFrame shape after initial load (and skipping rows 1-7): {df.shape}")
        if df.empty:
            print(
                "WARNING: DataFrame is empty after skipping initial rows and reading header. Check if original row 8 and beyond contain data.")
        else:
            print("INFO: Preview of data (first 3 rows with new header):")
            print(df.head(3))

        # Step 1b: Delete the original 9th row's data.
        # After skiprows=7, the original 9th row is now the first data row (index 0) in the DataFrame.
        if not df.empty:
            if 0 in df.index:  # Check if the first data row (original row 9) exists
                print("INFO: Deleting original row 9 (which is current data row at index 0)...")
                df = df.drop(index=0)
                df = df.reset_index(drop=True)  # Reset index to be continuous from 0
                print("INFO: Original row 9 deleted.")
                print(f"INFO: DataFrame shape after deleting original row 9: {df.shape}")
                if not df.empty:
                    print("INFO: Preview of data after deleting original row 9 (first 3 rows):")
                    print(df.head(3))
                else:
                    print("WARNING: DataFrame is empty after deleting original row 9.")
            else:
                print(
                    "WARNING: No data row at index 0 to delete (original row 9 might not have existed or was part of the header/skipped rows).")
        else:
            print("INFO: DataFrame was empty before attempting to delete original row 9, skipping this step.")

        # Step 2: Delete columns A and B (the first two columns of the current DataFrame).
        if not df.empty:
            if df.shape[1] >= 2:
                print("INFO: Deleting the first two columns (A and B)...")
                columns_to_drop = df.columns[[0, 1]]
                df = df.drop(columns=columns_to_drop)
                print("INFO: First two columns deleted.")
                print(f"INFO: DataFrame shape after deleting columns: {df.shape}")
                if not df.empty:
                    print("INFO: Preview of data after deleting columns (first 3 rows):")
                    print(df.head(3))
                else:
                    print("WARNING: DataFrame is empty after deleting columns.")
            elif df.shape[1] == 1:
                print("INFO: DataFrame has only one column. Deleting it as per 'delete column A' logic.")
                df = df.drop(columns=df.columns[0])
                print("INFO: Single column deleted.")
                print(f"INFO: DataFrame shape after deleting the only column: {df.shape}")
            else:
                print("INFO: DataFrame has no columns to delete.")
        else:
            print("INFO: DataFrame was empty before attempting to delete columns, skipping this step.")

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