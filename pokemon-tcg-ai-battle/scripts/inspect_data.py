import pandas as pd
import glob
import os

print("=== Raw Data Directory ===")
raw_dir = "data/raw"
files = glob.glob(os.path.join(raw_dir, "*"))
for f in files:
    if os.path.isfile(f):
        print(f"- {f} (Size: {os.path.getsize(f)} bytes)")

print("\n=== Card Data CSV Inspection ===")
for path in [f"{raw_dir}/EN_Card_Data.csv", f"{raw_dir}/JP_Card_Data.csv"]:
    if os.path.exists(path):
        print(f"\n--- File: {path} ---")
        df = pd.read_csv(path)
        print(f"Shape: {df.shape}")
        print("Columns:")
        for col in df.columns:
            null_count = df[col].isnull().sum()
            print(f"  - {col}: type={df[col].dtype}, nulls={null_count}")
        print("\nFirst 3 rows:")
        print(df.head(3).to_string())

print("\n=== Sample Submission Deck Inspection ===")
deck_path = f"{raw_dir}/sample_submission/sample_submission/deck.csv"
if os.path.exists(deck_path):
    df_deck = pd.read_csv(deck_path)
    print(f"Shape: {df_deck.shape}")
    print(df_deck.head(10).to_string())
else:
    print(f"Deck path not found at: {deck_path}")
