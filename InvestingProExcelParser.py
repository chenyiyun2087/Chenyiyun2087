import pandas as pd

def filter_file_data(input_file_path, output_csv_path):
    """
    Reads data from an Excel or CSV file, filters it based on specified criteria,
    and saves the filtered data to a new CSV file.

    Args:
        input_file_path (str): Path to the input Excel (.xlsx) or CSV (.csv) file.
        output_csv_path (str): Path to save the filtered CSV file.
    """
    df = None  # Initialize df to None
    try:
        # Read the file based on its extension
        if input_file_path.endswith('.xlsx'):
            print(f"INFO: Reading Excel file: {input_file_path}...")
            # For Excel files, use pd.read_excel()
            # You might need to install openpyxl: pip install openpyxl
            df = pd.read_excel(input_file_path, engine='openpyxl')
            print("INFO: Successfully read Excel file.")
        elif input_file_path.endswith('.csv'):
            print(f"INFO: Reading CSV file: {input_file_path}...")
            # Attempt to read CSV with common encodings if utf-8 fails
            try:
                df = pd.read_csv(input_file_path, encoding='utf-8')
                print("INFO: Successfully read CSV with 'utf-8' encoding.")
            except UnicodeDecodeError:
                print("WARNING: Failed to read CSV with 'utf-8' encoding, trying 'gbk'.")
                try:
                    df = pd.read_csv(input_file_path, encoding='gbk')
                    print("INFO: Successfully read CSV with 'gbk' encoding.")
                except UnicodeDecodeError:
                    print("WARNING: Failed to read CSV with 'gbk' encoding, trying 'latin1'.")
                    df = pd.read_csv(input_file_path, encoding='latin1')
                    print("INFO: Successfully read CSV with 'latin1' encoding (last resort).")
        else:
            print(f"ERROR: Unsupported file type for input file: {input_file_path}. Please use .xlsx or .csv.")
            return None

        if df is None: # Should not happen if logic above is correct, but as a safeguard
            print("ERROR: DataFrame was not loaded.")
            return None

        print("\n原始数据前几行 (Original DataFrame head):")
        print(df.head())
        print("\n原始数据信息 (Original DataFrame info):")
        df.info()
        print("\n--- 开始筛选 (Applying Filters) ---")

        df_filtered = df.copy()

        numeric_cols = ['市盈增长比率', '盈利评分', '市盈率(经调整)', '预期净利润增长率']
        numeric_cols = []
        for col in numeric_cols:
            if col in df_filtered.columns:
                df_filtered[col] = pd.to_numeric(df_filtered[col], errors='coerce')
            else:
                print(f"警告: 列 '{col}' 在文件中未找到。将跳过涉及此列的筛选条件。")

        # Initialize conditions to Series of True values
        condition1 = pd.Series([True] * len(df_filtered), index=df_filtered.index)
        condition2 = pd.Series([True] * len(df_filtered), index=df_filtered.index)
        condition3 = pd.Series([True] * len(df_filtered), index=df_filtered.index)
        condition4 = pd.Series([True] * len(df_filtered), index=df_filtered.index)
        # condition5 = pd.Series([True] * len(df_filtered), index=df_filtered.index) # Not used in final_condition in original script

        if '市盈增长比率' in df_filtered.columns and pd.api.types.is_numeric_dtype(df_filtered['市盈增长比率']):
            condition1 = (df_filtered['市盈增长比率'] >= 0) & (df_filtered['市盈增长比率'] <= 1)
        else:
            print("警告: '市盈增长比率' 列不存在或非数值类型，无法应用筛选条件1。")

        if '公允价值不确定性' in df_filtered.columns:
            condition2 = df_filtered['公允价值不确定性'] == '最低'
        else:
            print("警告: '公允价值不确定性' 列不存在，无法应用筛选条件2。")

        if '盈利评分' in df_filtered.columns and pd.api.types.is_numeric_dtype(df_filtered['盈利评分']):
            condition3 = df_filtered['盈利评分'] > 2
        else:
            print("警告: '盈利评分' 列不存在或非数值类型，无法应用筛选条件3。")

        if '市盈率(经调整)' in df_filtered.columns and pd.api.types.is_numeric_dtype(df_filtered['市盈率(经调整)']):
            condition4 = df_filtered['市盈率(经调整)'] > 0
        else:
            print("警告: '市盈率(经调整)' 列不存在或非数值类型，无法应用筛选条件4。")

        # Condition 5 was defined but not used in the final_condition in your original script
        # if '预期净利润增长率' in df_filtered.columns and pd.api.types.is_numeric_dtype(df_filtered['预期净利润增长率']):
        #     condition5 = df_filtered['预期净利润增长率'] > 0
        # else:
        #     print("警告: '预期净利润增长率' 列不存在或非数值类型，无法应用筛选条件5。")

        final_condition = condition1 & condition2 & condition3 & condition4
        result_df = df_filtered[final_condition].copy()

        print("\n筛选后数据前几行 (Filtered DataFrame head):")
        print(result_df.head())
        print(f"\n原始数据行数 (Number of rows in original data): {len(df)}")
        print(f"筛选后数据行数 (Number of rows after filtering): {len(result_df)}")

        result_df.to_csv(output_csv_path, index=False, encoding='utf-8-sig')
        print(f"\n筛选后的数据已保存到 (Filtered data saved to) {output_csv_path}")

        return result_df

    except FileNotFoundError:
        print(f"错误: 文件 {input_file_path} 未找到。(Error: The file {input_file_path} was not found.)")
    except pd.errors.EmptyDataError:
        print(f"错误: 文件 {input_file_path} 为空。(Error: The file {input_file_path} is empty.)")
    except UnicodeDecodeError as e: # Should primarily be caught by specific CSV logic now
        print(f"错误: 读取CSV文件时发生编码错误。 (Error: Encoding error when reading CSV.) Original error: {e}")
    except KeyError as e:
        print(f"错误: 文件中缺少筛选所需的列: {e}。(Error: A column required for filtering was not found in the file: {e})")
    except Exception as e:
        print(f"发生意外错误 (An unexpected error occurred): {e}")
        import traceback
        traceback.print_exc() # Print full traceback for unexpected errors
    return None # Return None if any error occurs

# --- 主程序执行 (Main execution) ---
if __name__ == "__main__":
    # Ensure this is the correct path to your Excel file
    input_file = 'cyy - cyy - 2025-05-28.csv'
    output_file = 'cyy - cyy - 2025-05-28-filtered.csv' # Changed output name slightly for clarity

    print(f"开始处理文件 (Starting the filtering process for) {input_file}...")
    filtered_data = filter_file_data(input_file, output_file)

    if filtered_data is not None:
        print("\n筛选过程完成。(Filtering process completed.)")
    else:
        print("\n筛选过程遇到错误或未返回数据。(Filtering process encountered an error or returned no data.)")