"""
vm_seeder.py — Seeds MongoDB 'azure' collection with VM instance hierarchy.

Reads from Azure VM metadata API (same URL vm.py uses).
Writes one document per instance — no pricing, no per-region duplication.

Run once (or on a schedule to refresh):
    python vm_seeder.py

Requires in backend/.env:
    MONGO_URI=mongodb+srv://<user>:<pass>@cluster0.xxxxx.mongodb.net/
"""

import os
import sys
import requests
from datetime import datetime, timezone
from pymongo import MongoClient, UpdateOne
from dotenv import load_dotenv

load_dotenv()

MONGO_URI   = os.getenv("MONGO_URI")
DB_NAME     = "Cloud"
COLLECTION  = "azure"

VM_METADATA_URL = "https://azure.microsoft.com/api/v4/pricing/virtual-machines/metadata/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://azure.microsoft.com/en-us/pricing/calculator/",
}


def validate_env():
    """Fail fast before any network calls if env is misconfigured."""
    if not MONGO_URI:
        print("ERROR: MONGO_URI not set in .env")
        sys.exit(1)


def fetch_metadata():
    print("Fetching Azure VM metadata...")
    resp = requests.get(VM_METADATA_URL, headers=HEADERS, timeout=45)
    resp.raise_for_status()
    return resp.json()


def _get_offer_specs(offers, instance_slug):
    """
    Look up vCPUs, RAM, and other specs from offers.
    Tries both windows and linux variants of the instance, returns first match.
    """
    specs = {}
    # Try to find the offer data for this instance
    # Offers are keyed as "{os}-{size}-{tier}", try common tiers
    for os_type in ["linux", "windows"]:
        for tier in ["standard", "premium", "standardssd", "premiumssd"]:
            offer_key = f"{os_type}-{instance_slug}-{tier}"
            if offer_key in offers:
                offer = offers[offer_key]
                # Extract available specs
                if "cores" in offer:
                    specs["vcpus"] = int(float(offer["cores"]))
                if "ram" in offer:
                    specs["ram"] = float(offer["ram"])
                if "diskSize" in offer:
                    specs["diskSize"] = offer["diskSize"]
                if specs:  # Return first match with any specs
                    return specs
    return specs


def build_documents(meta):
    """
    Parse meta['dropdown'] into flat documents.

    meta['dropdown'] shape:
    [
      {
        "slug": "all",
        "displayName": "All",
        "series": [ { "slug": "Dsv3", "displayName": "Dsv3 Series", "instances": [...] } ]
      },
      {
        "slug": "general-purpose",
        "displayName": "General Purpose",
        "series": [ ... ]
      },
      ...
    ]

    We skip the "all" category — it's just a flat duplicate of everything else.
    Each real category → series → instance becomes one document.

    Instance documents include vcpus, ram, and diskSize from both instance metadata
    and fallback to offers lookup.
    """
    allowed_slugs = {s["slug"] for s in meta.get("sizesPayGo", [])}
    categories    = [c for c in meta.get("dropdown", []) if c["slug"] != "all"]
    offers        = meta.get("offers", {})
    now           = datetime.now(timezone.utc)

    docs          = []
    seen          = set()   # (category_slug, series_slug, instance_slug) — deduplicate
    skipped_paygo = 0
    skipped_dupe  = 0

    for cat in categories:
        cat_slug    = cat.get("slug", "")
        cat_display = cat.get("displayName", cat_slug)

        for series in cat.get("series", []):
            series_slug    = series.get("slug", "")
            series_display = series.get("displayName", series_slug)

            for inst in series.get("instances", []):
                slug = inst.get("slug", "")
                if not slug:
                    continue

                if slug not in allowed_slugs:
                    skipped_paygo += 1
                    continue

                key = (cat_slug, series_slug, slug)
                if key in seen:
                    skipped_dupe += 1
                    continue
                seen.add(key)

                # Extract hardware specs — first try instance metadata, then fall back to offers
                vcpus    = inst.get("cores")
                ram      = inst.get("ram")
                disk     = inst.get("diskSize")
                
                # Fallback to offers lookup if specs not in instance metadata
                if vcpus is None or ram is None or disk is None:
                    offer_specs = _get_offer_specs(offers, slug)
                    if vcpus is None:
                        vcpus = offer_specs.get("vcpus")
                    if ram is None:
                        ram = offer_specs.get("ram")
                    if disk is None:
                        disk = offer_specs.get("diskSize")
                
                raw_name = inst.get("displayName", slug)
                display  = (raw_name
                            .replace("{0}", str(vcpus) if vcpus is not None else "?")
                            .replace("{1}", str(ram)   if ram   is not None else "?")
                            .replace("{2}", str(disk)  if disk  is not None else "—"))

                doc = {
                    "provider":         "azure",
                    "category":         cat_slug,
                    "category_display": cat_display,
                    "series":           series_slug,
                    "series_display":   series_display,
                    "slug":             slug,
                    "displayName":      display,
                    "updatedAt":        now,
                }

                # Only include hardware specs if Azure actually provides them,
                # so we don't store misleading nulls.
                if vcpus is not None:
                    doc["vcpus"] = int(float(vcpus))
                if ram is not None:
                    doc["ram"] = float(ram)
                if disk is not None:
                    doc["diskSize"] = disk

                docs.append(doc)

    print(f"  Skipped (not PayGo): {skipped_paygo}")
    print(f"  Skipped (duplicate): {skipped_dupe}")
    return docs


def seed(docs):
    client = MongoClient(MONGO_URI)
    col    = client[DB_NAME][COLLECTION]

    # Upsert — safe to re-run without duplicates.
    # The filter uses the natural compound key; $set refreshes all fields including updatedAt.
    ops = [
        UpdateOne(
            {
                "provider": d["provider"],
                "category": d["category"],
                "series":   d["series"],
                "slug":     d["slug"],
            },
            {"$set": d},
            upsert=True,
        )
        for d in docs
    ]

    result = col.bulk_write(ops)
    print(f"Upserted: {result.upserted_count}  Modified: {result.modified_count}  Total: {len(docs)}")

    # Indexes for the three cascade endpoints:
    #   /instances/categories  — distinct on (provider, category)
    #   /instances/series      — filter by (provider, category), distinct on series
    #   /instances/sizes       — filter by (provider, category, series)
    col.create_index([("provider", 1), ("category", 1)])
    col.create_index([("provider", 1), ("category", 1), ("series", 1)])
    # Drop old index if it exists (without unique constraint) before creating new one
    try:
        col.drop_index("provider_1_slug_1")
    except:
        pass
    col.create_index([("provider", 1), ("slug", 1)], unique=True,
                     partialFilterExpression={"provider": {"$exists": True}})
    print("Indexes ensured.")

    client.close()


if __name__ == "__main__":
    validate_env()
    meta = fetch_metadata()
    docs = build_documents(meta)
    print(f"Built {len(docs)} instance documents.")
    seed(docs)
    print("Done.")
