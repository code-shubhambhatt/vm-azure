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

VM_METADATA_URL   = "https://azure.microsoft.com/api/v4/pricing/virtual-machines/metadata/"
VM_CALCULATOR_URL = (
    "https://azure.microsoft.com/api/v4/pricing/virtual-machines/"
    "calculator/{region}/?culture=en-us&discount=mca"
)

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
    for os_type in ["linux", "windows"]:
        for tier in ["standard", "premium", "standardssd", "premiumssd"]:
            offer_key = f"{os_type}-{instance_slug}-{tier}"
            if offer_key in offers:
                offer = offers[offer_key]
                if "cores" in offer:
                    specs["vcpus"] = int(float(offer["cores"]))
                if "ram" in offer:
                    specs["ram"] = float(offer["ram"])
                if "diskSize" in offer:
                    specs["diskSize"] = offer["diskSize"]
                if specs:
                    return specs
    return specs


def _build_instance_info(inst, offers):
    """
    Resolve display name + hardware specs for a single instance dict.
    Returns (slug, display_name, spec_dict).
    """
    slug = inst.get("slug", "")
    vcpus = inst.get("cores")
    ram   = inst.get("ram")
    disk  = inst.get("diskSize")

    if vcpus is None or ram is None or disk is None:
        fallback = _get_offer_specs(offers, slug)
        if vcpus is None:
            vcpus = fallback.get("vcpus")
        if ram is None:
            ram = fallback.get("ram")
        if disk is None:
            disk = fallback.get("diskSize")

    raw_name = inst.get("displayName", slug)
    display  = (raw_name
                .replace("{0}", str(vcpus) if vcpus is not None else "?")
                .replace("{1}", str(ram)   if ram   is not None else "?")
                .replace("{2}", str(disk)  if disk  is not None else "—"))

    spec = {}
    if vcpus is not None:
        spec["vcpus"] = int(float(vcpus))
    if ram is not None:
        spec["ram"] = float(ram)
    if disk is not None:
        spec["diskSize"] = disk

    return slug, display, spec


def fetch_available_slugs_for_region(region_slug, all_slugs):
    """
    Call the v4 calculator for one region and return the set of
    instance slugs that have at least one offer key there.
    """
    url = VM_CALCULATOR_URL.format(region=region_slug)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=45)
        resp.raise_for_status()
        offers = resp.json().get("offers", {})
    except Exception as e:
        print(f"    WARN: Could not fetch {region_slug}: {e}")
        return set()

    available = set()
    for offer_key in offers:
        parts = offer_key.split("-", 1)
        if parts[0] not in ("linux", "windows") or len(parts) < 2:
            continue
        rest = parts[1]
        for slug in all_slugs:
            if rest == slug or rest.startswith(slug + "-"):
                available.add(slug)
                break
    return available


def build_documents(meta):
    """
    New document shape — one doc per (region, category, series):

    {
        provider:         "azure",
        region:           "us-east",
        category:         "general-purpose",
        category_display: "General Purpose",
        series:           "Dsv5",
        series_display:   "Dsv5 Series",
        instances: [
            { slug, displayName, vcpus, ram, diskSize },
            ...
        ],
        updatedAt: <datetime>
    }

    Steps:
    1. Build a global map: { (cat, series) -> { slug -> {displayName, specs} } }
       using the metadata + global offers (for spec lookup only).
    2. For each region, fetch available slugs from the v4 calculator.
    3. For each (region, cat, series), collect only available instances → one doc.
    """
    allowed_slugs  = {s["slug"] for s in meta.get("sizesPayGo", [])}
    categories     = [c for c in meta.get("dropdown", []) if c["slug"] != "all"]
    global_offers  = meta.get("offers", {})   # used for spec lookup only
    regions        = meta.get("regions", [])
    now            = datetime.now(timezone.utc)

    # ── Step 1: build global instance map ─────────────────────────────────────
    # global_map[(cat_slug, series_slug)] = [
    #   { slug, displayName, vcpus?, ram?, diskSize?,
    #     cat_display, series_display }
    # ]
    global_map    = {}
    all_slugs     = set()

    for cat in categories:
        cat_slug    = cat.get("slug", "")
        cat_display = cat.get("displayName", cat_slug)

        for series in cat.get("series", []):
            series_slug    = series.get("slug", "")
            series_display = series.get("displayName", series_slug)
            key            = (cat_slug, series_slug)

            for inst in series.get("instances", []):
                slug = inst.get("slug", "")
                if not slug or slug not in allowed_slugs:
                    continue
                slug, display, spec = _build_instance_info(inst, global_offers)
                inst_doc = {"slug": slug, "displayName": display, **spec}
                global_map.setdefault(key, {
                    "cat_display":    cat_display,
                    "series_display": series_display,
                    "instances":      {},
                })
                global_map[key]["instances"][slug] = inst_doc
                all_slugs.add(slug)

    print(f"Global: {len(all_slugs)} unique slugs across {len(global_map)} (category, series) pairs.")

    # ── Step 2: per-region availability ───────────────────────────────────────
    docs    = []
    total_r = len(regions)

    for i, region in enumerate(regions, 1):
        r_slug = region.get("slug", "")
        if not r_slug:
            continue

        print(f"  [{i}/{total_r}] {r_slug} ...", end=" ", flush=True)
        available = fetch_available_slugs_for_region(r_slug, all_slugs)
        print(f"{len(available)} instances available")

        # ── Step 3: build one doc per (region, cat, series) ───────────────────
        for (cat_slug, series_slug), series_data in global_map.items():
            region_instances = [
                inst_doc
                for slug, inst_doc in series_data["instances"].items()
                if slug in available
            ]
            if not region_instances:
                continue  # this series has nothing available here — skip

            # Sort by slug for deterministic output
            region_instances.sort(key=lambda x: x["slug"])

            docs.append({
                "provider":         "azure",
                "region":           r_slug,
                "category":         cat_slug,
                "category_display": series_data["cat_display"],
                "series":           series_slug,
                "series_display":   series_data["series_display"],
                "instances":        region_instances,
                "updatedAt":        now,
            })

    return docs


def seed(docs):
    client = MongoClient(MONGO_URI)
    col    = client[DB_NAME][COLLECTION]

    # Drop old schema indexes (if they exist from prior runs)
    try:
        col.drop_index("provider_1_slug_1")
        print("Dropped old provider_1_slug_1 index.")
    except Exception:
        pass  # Index doesn't exist, that's fine

    # Upsert keyed on (provider, region, category, series) — safe to re-run.
    ops = [
        UpdateOne(
            {
                "provider": d["provider"],
                "region":   d["region"],
                "category": d["category"],
                "series":   d["series"],
            },
            {"$set": d},
            upsert=True,
        )
        for d in docs
    ]

    result = col.bulk_write(ops)
    print(f"Upserted: {result.upserted_count}  Modified: {result.modified_count}  Total: {len(docs)}")

    # ── Indexes ────────────────────────────────────────────────────────────────
    # Categories endpoint  — distinct(category) WHERE provider=azure  (global, no region)
    col.create_index([("provider", 1), ("category", 1)])

    # Series endpoint      — WHERE provider + region + category
    col.create_index([("provider", 1), ("region", 1), ("category", 1)])

    # Sizes endpoint       — WHERE provider + region + category + series (point lookup)
    col.create_index(
        [("provider", 1), ("region", 1), ("category", 1), ("series", 1)],
        unique=True,
        partialFilterExpression={"provider": {"$exists": True}},
    )

    print("Indexes ensured.")
    client.close()


if __name__ == "__main__":
    validate_env()
    meta = fetch_metadata()
    print("Building region-keyed documents (fetches one API call per region)...")
    docs = build_documents(meta)
    print(f"Built {len(docs)} (region × category × series) documents.")
    seed(docs)
    print("Done.")