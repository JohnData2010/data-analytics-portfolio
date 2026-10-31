"""Download both City of Melbourne datasets and their portal metadata into data/raw/."""
import urllib.request
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
API = "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets"
SLUGS = ["customer-service-requests-with-resolution-time",
         "customer-service-requests-with-digital-channel-information"]

RAW.mkdir(parents=True, exist_ok=True)
for slug in SLUGS:
    urllib.request.urlretrieve(f"{API}/{slug}/exports/csv?delimiter=%2C", RAW / f"{slug}.csv")
    urllib.request.urlretrieve(f"{API}/{slug}", RAW / f"{slug}.metadata.json")
    print("downloaded", slug)
