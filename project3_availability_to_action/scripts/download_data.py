"""Download FreshRetailNet-50K (train + eval parquet, dataset card) from Hugging Face into data/raw/."""
import urllib.request
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
BASE = "https://huggingface.co/datasets/Dingdong-Inc/FreshRetailNet-50K/resolve/main"
FILES = {"data/train.parquet": "train.parquet", "data/eval.parquet": "eval.parquet",
         "README.md": "dataset_card.md"}

RAW.mkdir(parents=True, exist_ok=True)
for src, dst in FILES.items():
    urllib.request.urlretrieve(f"{BASE}/{src}", RAW / dst)
    print("downloaded", dst)
