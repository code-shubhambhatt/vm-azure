"""
vm_seeder.py - Seeds MongoDB 'azure' collection with VM instance hierarchy.

Reads Azure VM metadata plus the region calculator API.
Writes one document per (region, operatingSystem, category).
Each document nests series and sizes, with tier availability stored on sizes.
"""

import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = "Cloud"
COLLECTION = "azure"

VM_METADATA_URL = "https://azure.microsoft.com/api/v4/pricing/virtual-machines/metadata/"
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

OS_LABELS = {
    "linux": "Linux",
    "windows": "Windows",
}

LINUX_OFFER_CANDIDATES = {
    "ubuntu": ([], None),
    "ubuntu-pro": (["ubuntu-pro"], "per-core"),
    "ubuntu-advantage": (["ubuntu-advantage-essential", "ubuntu-advantage-standard", "ubuntu-advantage"], "flat"),
    "redhat": (["redhat", "rhel"], "per-core"),
    "rhel-ha": (["rhel-ha"], "per-core"),
    "rhel-sap-business": (["rhel-sap-business", "rhel-sap-business-applications"], "per-core"),
    "rhel-sap-ha": (["rhel-sap-ha", "rhel-sap-hana-ha"], "per-core"),
    "sles-enterprise": (["sles-basic", "sles-enterprise"], "per-core"),
    "sles-hpc": (["sles-hpc-standard", "sles-hpc"], "per-core"),
    "sles-sap-priority": (["sles-sap", "sles-sap-priority"], "per-core"),
    "sql-redhat": ([
        "sql-server-enterprise-redhat",
        "sql-server-redhat-enterprise",
        "sql-server-redhat-standard",
        "sql-redhat-standard",
        "sql-redhat",
    ], "per-core"),
    "sql-sles-priority": ([
        "sql-server-enterprise-sles",
        "sql-server-sles-enterprise",
        "sql-server-sles-priority-enterprise",
        "sql-server-sles-standard",
        "sql-sles-priority-standard",
        "sql-sles-priority",
    ], "per-core"),
    "sql-linux": ([
        "sql-server-enterprise-linux",
        "sql-server-linux-enterprise",
        "sql-server-linux-standard",
        "sql-linux-standard",
        "sql-linux",
    ], "per-core"),
    "sql-ubuntu-pro": ([
        "sql-server-developer-ubuntu-pro",
        "sql-server-ubuntu-pro-developer",
        "ubuntu-pro",
        "sql-server-ubuntu-pro-standard",
        "sql-ubuntu-pro-standard",
        "sql-ubuntu-pro",
    ], "per-core"),
}


def validate_env():
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
    Look up vCPUs, RAM, and disk size from the live calculator offers.
    Tries both linux and windows variants of the instance.
    """
    specs = {}
    for os_type in ("linux", "windows"):
        for tier in ("standard", "premium", "standardssd", "premiumssd"):
            offer_key = f"{os_type}-{instance_slug}-{tier}"
            offer = offers.get(offer_key)
            if not offer:
                continue
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
    slug = inst.get("slug", "")
    vcpus = inst.get("cores")
    ram = inst.get("ram")
    disk = inst.get("diskSize")

    if vcpus is None or ram is None or disk is None:
        fallback = _get_offer_specs(offers, slug)
        if vcpus is None:
            vcpus = fallback.get("vcpus")
        if ram is None:
            ram = fallback.get("ram")
        if disk is None:
            disk = fallback.get("diskSize")

    raw_name = inst.get("displayName", slug)
    display = (
        raw_name.replace("{0}", str(vcpus) if vcpus is not None else "?")
        .replace("{1}", str(ram) if ram is not None else "?")
        .replace("{2}", str(disk) if disk is not None else "-")
    )

    spec = {}
    if vcpus is not None:
        spec["vcpus"] = int(float(vcpus))
    if ram is not None:
        spec["ram"] = float(ram)
    if disk is not None:
        spec["diskSize"] = disk

    return slug, display, spec


def _offer_supports_linux_type(offers, linux_type_slug, cores):
    entry = LINUX_OFFER_CANDIDATES.get(linux_type_slug)
    if not entry:
        return False

    candidates, model = entry
    if not candidates or model is None:
        return True

    cores_str = str(int(cores)) if cores is not None else ""
    for prefix in candidates:
        for key in offers:
            if not key.startswith(prefix):
                continue
            if model == "flat":
                return True
            if cores_str and f"-{cores_str}-core" in key:
                return True
            if cores_str and key.endswith(f"-{cores_str}-core"):
                return True
    return False


def _build_linux_type_tiers(offers, size_slug, tiers, linux_types, region_slug, cores):
    """
    Build { linux_type_slug: [tier, ...] } for a size in a region.

    We only keep linux types that have a real software offer for at least one
    supported compute tier, so the selector never exposes a combination that
    later fails at price time.
    """
    supported = {}
    for linux_type_slug, _ in linux_types:
        supported_tiers = []
        for tier in tiers:
            if _offer_supports_linux_type(offers, linux_type_slug, cores):
                supported_tiers.append(tier)
        if supported_tiers:
            supported[linux_type_slug] = supported_tiers
    return supported


def _get_linux_types_from_metadata(meta):
    raw = meta.get("linuxTypes", [])
    if not raw:
        return [("ubuntu", "Ubuntu")]
    result = []
    for item in raw:
        if isinstance(item, dict):
            slug = item.get("slug", "")
            name = item.get("displayName", slug)
        else:
            slug = name = str(item)
        if slug:
            result.append((slug, name))
    return result


def fetch_available_slugs_for_region(region_slug, all_slugs):
    """
    Call the v4 calculator for one region and return:
      { os_type: { size_slug: {tier, ...} } }
    so availability can be filtered by OS and tier without storing one row per
    series.
    """
    url = VM_CALCULATOR_URL.format(region=region_slug)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=45)
        resp.raise_for_status()
        offers = resp.json().get("offers", {})
    except Exception as exc:
        print(f"    WARN: Could not fetch {region_slug}: {exc}")
        return {"linux": {}, "windows": {}}

    available = {"linux": {}, "windows": {}}
    for offer_key in offers:
        parts = offer_key.split("-", 1)
        if len(parts) < 2 or parts[0] not in available:
            continue
        os_type = parts[0]
        rest = parts[1]
        size_part, tier = rest.rsplit("-", 1) if "-" in rest else (rest, "")
        if tier not in ("standard", "basic"):
            continue
        for slug in all_slugs:
            if size_part == slug or size_part.startswith(slug + "-"):
                available[os_type].setdefault(slug, set()).add(tier)
                break
    return available


def build_documents(meta):
    """
    Build one document per (region, operatingSystem, category).
    """
    allowed_slugs = {s["slug"] for s in meta.get("sizesPayGo", [])}
    categories = [c for c in meta.get("dropdown", []) if c.get("slug") != "all"]
    global_offers = meta.get("offers", {})
    regions = meta.get("regions", [])
    linux_types = _get_linux_types_from_metadata(meta)
    now = datetime.now(timezone.utc)

    all_slugs = set()

    for cat in categories:
        for series in cat.get("series", []):
            for inst in series.get("instances", []):
                slug = inst.get("slug", "")
                if not slug or slug not in allowed_slugs:
                    continue
                _build_instance_info(inst, global_offers)
                all_slugs.add(slug)
    print(f"Global: {len(all_slugs)} unique slugs across {len(categories)} categories.")

    docs = []
    total_r = len(regions)
    for i, region in enumerate(regions, 1):
        r_slug = region.get("slug", "")
        if not r_slug:
            continue

        print(f"  [{i}/{total_r}] {r_slug} ...", end=" ", flush=True)
        available_by_os = fetch_available_slugs_for_region(r_slug, all_slugs)
        print(
            "linux="
            f"{len(available_by_os['linux'])} "
            f"windows={len(available_by_os['windows'])}"
        )

        for os_slug, os_sizes in available_by_os.items():
            for cat in categories:
                cat_slug = cat.get("slug", "")
                cat_display = cat.get("displayName", cat_slug)
                series_docs = []

                for series in cat.get("series", []):
                    series_slug = series.get("slug", "")
                    series_display = series.get("displayName", series_slug)
                    size_docs = []

                    for inst in series.get("instances", []):
                        slug = inst.get("slug", "")
                        if not slug or slug not in allowed_slugs:
                            continue
                        tiers = sorted(os_sizes.get(slug, set()))
                        if not tiers:
                            continue
                        slug, display, spec = _build_instance_info(inst, global_offers)
                        linux_type_tiers = {}
                        if os_slug == "linux":
                            linux_type_tiers = _build_linux_type_tiers(
                                global_offers,
                                slug,
                                tiers,
                                linux_types,
                                r_slug,
                                spec.get("vcpus"),
                            )
                            if not linux_type_tiers:
                                continue
                        size_docs.append(
                            {
                                "slug": slug,
                                "displayName": display,
                                **spec,
                                "tiers": tiers,
                                **({"linuxTypes": linux_type_tiers} if linux_type_tiers else {}),
                            }
                        )

                    if not size_docs:
                        continue

                    size_docs.sort(key=lambda x: x["slug"])
                    series_docs.append(
                        {
                            "slug": series_slug,
                            "displayName": series_display,
                            "sizes": size_docs,
                        }
                    )

                if not series_docs:
                    continue

                docs.append(
                    {
                        "provider": "azure",
                        "region": r_slug,
                        "operatingSystem": os_slug,
                        "operatingSystem_display": OS_LABELS.get(os_slug, os_slug),
                        "category": cat_slug,
                        "category_display": cat_display,
                        "series": series_docs,
                        "updatedAt": now,
                    }
                )

    return docs


def seed(docs):
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME][COLLECTION]

    for index_name in (
        "provider_1_slug_1",
        "provider_1_region_1_category_1_series_1",
        "provider_1_region_1_category_1",
        "provider_1_category_1",
    ):
        try:
            col.drop_index(index_name)
            print(f"Dropped old {index_name} index.")
        except Exception:
            pass

    if not docs:
        print("No documents to seed.")
        client.close()
        return

    col.delete_many({"provider": "azure"})
    print("Cleared existing azure docs.")

    ops = [
        UpdateOne(
            {
                "provider": d["provider"],
                "region": d["region"],
                "operatingSystem": d["operatingSystem"],
                "category": d["category"],
            },
            {"$set": d},
            upsert=True,
        )
        for d in docs
    ]

    if ops:
        result = col.bulk_write(ops)
        print(
            f"Upserted: {result.upserted_count}  Modified: {result.modified_count}  Total: {len(docs)}"
        )
    else:
        print("No documents to seed.")

    # Keep the lookup deterministic and enforce one row per region/OS/category combination.
    col.create_index(
        [("provider", 1), ("region", 1), ("operatingSystem", 1), ("category", 1)],
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
    print(f"Built {len(docs)} (region x OS x category) documents.")
    seed(docs)
    print("Done.")
