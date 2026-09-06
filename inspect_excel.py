import os
import pandas as pd

files = os.listdir('uploads')
last_file = os.path.join('uploads', files[-1])
df = pd.read_excel(last_file)

with open('excel_info.txt', 'w', encoding='utf-8') as f:
    f.write(f"File: {files[-1]}\n")
    f.write(f"Columns: {df.columns.tolist()}\n")
    f.write(f"First row: {df.iloc[0].tolist()}\n")
