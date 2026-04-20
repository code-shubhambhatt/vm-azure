"""
vm.py — Azure Virtual Machines pricing service
Endpoints: /api/vm/*

Billing: PAYG only.
Windows: License Included or Azure Hybrid Benefit.
Linux subtypes: populated dynamically from VM metadata linuxTypes array.

Instance selector endpoints (MongoDB-backed, no Azure call):
  /vm/instances/categories   — all distinct categories
  /vm/instances/series       — series filtered by ?category=
  /vm/instances/sizes        — sizes filtered by ?category= & ?series=

Add-on sections:
  /vm/managed-disks/*        — Tier + Redundancy + Disk Size + Count
  /vm/storage-transactions/* — inherits disk tier from managed disks
  /vm/bandwidth/*            — Data Transfer Type, Source Region, Destination Region, GB
"""

import os
from flask import request
from flask_restx import Namespace, Resource, fields
from pymongo import MongoClient
from dotenv import load_dotenv
from .shared import fetch_pricing, fetch_metadata, get_graduated_price

load_dotenv()

ns = Namespace("vm", description="Azure Virtual Machines pricing")

# ── MongoDB ───────────────────────────────────────────────────────────────────
_mongo_client = None

def _get_collection():
    global _mongo_client
    if _mongo_client is None:
        uri = os.getenv("MONGO_URI")
        if not uri:
            raise RuntimeError("MONGO_URI not set")
        _mongo_client = MongoClient(uri)
    return _mongo_client["Cloud"]["azure"]

# ── API URLs ──────────────────────────────────────────────────────────────────
VM_METADATA_URL   = "https://azure.microsoft.com/api/v4/pricing/virtual-machines/metadata/"
VM_CALCULATOR_URL = (
    "https://azure.microsoft.com/api/v4/pricing/virtual-machines/"
    "calculator/{region}/?culture=en-us&discount=mca"
)

# v2 disk endpoint — offer keys like "standardssd-e10-lrs", prices region-keyed directly
DISK_CALCULATOR_URL = (
    "https://azure.microsoft.com/api/v2/pricing/managed-disks/calculator/"
)

# v2 bandwidth endpoint — single global URL, zone-based pricing
# Offer keys: "interregion-zone{src}-zone{dest}" for inter-region
#             "internet-egress-zone{src}"         for internet egress
# Azure maps each region to a billing zone (1-3, or "de" for Germany)
BW_CALCULATOR_URL = (
    "https://azure.microsoft.com/api/v2/pricing/bandwidth/calculator"
)

# Azure billing zone map — regions grouped by outbound pricing zone
# Zone 1: North America, Europe (cheapest)
# Zone 2: Asia Pacific, Japan, India, Korea, Australia/NZ
# Zone 3: Brazil, South Africa, Middle East (most expensive inter-region)
# Source: https://azure.microsoft.com/en-us/pricing/details/bandwidth/
REGION_TO_ZONE = {
    # Zone 1 — North America
    "us-east":           "1", "us-east-2":       "1", "us-west":         "1",
    "us-west-2":         "1", "us-west-3":       "1", "us-central":      "1",
    "us-north-central":  "1", "us-south-central":"1", "us-west-central": "1",
    "canada-east":       "1", "canada-central":  "1",
    # Zone 1 — Europe
    "europe-north":      "1", "europe-west":     "1",
    "uk-south":          "1", "uk-west":         "1",
    "france-central":    "1", "france-south":    "1",
    "switzerland-north": "1", "switzerland-west":"1",
    "norway-east":       "1", "norway-west":     "1",
    "sweden-central":    "1",
    # Zone 2 — Asia Pacific
    "asia-pacific-east": "2", "asia-pacific-southeast": "2",
    "japan-east":        "2", "japan-west":     "2",
    "korea-central":     "2", "korea-south":    "2",
    # Zone 2 — India
    "central-india":     "2", "south-india":    "2", "west-india": "2",
    # Zone 2 — Australia / NZ
    "australia-east":    "2", "australia-southeast": "2",
    "australia-central": "2", "australia-central-2": "2",
    "new-zealand-north": "2",
    # Zone 3 — South America
    "brazil-south":      "3", "brazil-southeast":"3",
    # Zone 3 — Middle East / Africa
    "uae-north":         "3", "uae-central":    "3",
    "south-africa-north":"3", "south-africa-west":"3",
    "israel-central":    "3",
    "qatar-central":     "3",
    # Germany sovereign
    "germany-north":     "de","germany-west-central":"de",
}

# ── Disk tier definitions ─────────────────────────────────────────────────────
# (display_name, size_prefix, redundancy_options, offer_prefix)
# Offer key:  HDD -> "{prefix}-{size}"         e.g. "standardhdd-s10"
#            SSD -> "{prefix}-{size}-{redund}" e.g. "standardssd-e10-lrs"
DISK_TIERS = {
    "standardhdd": ("Standard HDD", "s", ["lrs"],        "standardhdd"),
    "standardssd": ("Standard SSD", "e", ["lrs", "zrs"], "standardssd"),
    "premiumssd":  ("Premium SSD",  "p", ["lrs", "zrs"], "premiumssd"),
}

DISK_SIZES = {
    "s": [
        ("s4","S4: 32 GiB"),("s6","S6: 64 GiB"),("s10","S10: 128 GiB"),
        ("s15","S15: 256 GiB"),("s20","S20: 512 GiB"),("s30","S30: 1 TiB"),
        ("s40","S40: 2 TiB"),("s50","S50: 4 TiB"),("s60","S60: 8 TiB"),
        ("s70","S70: 16 TiB"),("s80","S80: 32 TiB"),
    ],
    "e": [
        ("e1","E1: 4 GiB"),("e2","E2: 8 GiB"),("e3","E3: 16 GiB"),
        ("e4","E4: 32 GiB"),("e6","E6: 64 GiB"),("e10","E10: 128 GiB"),
        ("e15","E15: 256 GiB"),("e20","E20: 512 GiB"),("e30","E30: 1 TiB"),
        ("e40","E40: 2 TiB"),("e50","E50: 4 TiB"),("e60","E60: 8 TiB"),
        ("e70","E70: 16 TiB"),("e80","E80: 32 TiB"),
    ],
    "p": [
        ("p1","P1: 4 GiB"),("p2","P2: 8 GiB"),("p3","P3: 16 GiB"),
        ("p4","P4: 32 GiB"),("p6","P6: 64 GiB"),("p10","P10: 128 GiB"),
        ("p15","P15: 256 GiB"),("p20","P20: 512 GiB"),("p30","P30: 1 TiB"),
        ("p40","P40: 2 TiB"),("p50","P50: 4 TiB"),("p60","P60: 8 TiB"),
        ("p70","P70: 16 TiB"),("p80","P80: 32 TiB"),
    ],
}

MONTHLY_HOURS = 730.0

# Max paid transactions per hour from Azure Managed Disks pricing tables.
# Standard SSD limits differ by redundancy. Standard HDD limits only apply
# to the SKUs Azure documents with a paid-transaction ceiling.
STANDARD_SSD_TXN_LIMITS = {
    "lrs": {
        "e1": 6800, "e2": 13400, "e3": 26600, "e4": 43400,
        "e6": 81200, "e10": 147200, "e15": 274000, "e20": 502000,
        "e30": 829200, "e40": 893000, "e50": 1578400, "e60": 2777500,
        "e70": 4379300, "e80": 9478400,
    },
    "zrs": {
        "e1": 7800, "e2": 15400, "e3": 30600, "e4": 61000,
        "e6": 114400, "e10": 214000, "e15": 398400, "e20": 737600,
        "e30": 1238800, "e40": 1344700, "e50": 2405600, "e60": 4243900,
        "e70": 7353100, "e80": 14706200,
    },
}

STANDARD_HDD_TXN_LIMITS = {
    "s4": 450000,
    "s6": 858000,
    "s70": 93000000,
    "s80": 110000000,
}

TRANSFER_TYPES = [
    ("interregion",     "Inter Region"),
    ("internetegress",  "Internet Egress"),
]

ROUTED_VIA_OPTIONS = [
    ("premium", "Microsoft Global Network"),
    ("isp",     "Public Internet"),
]

# Internet egress rates — first 100 GB/month free, then per-GB for "next 10 TB" tier
# Source: https://azure.microsoft.com/en-us/pricing/details/bandwidth/
INTERNET_EGRESS_RATES = {
    # Microsoft Premium Global Network
    ("premium", "1"):  0.087,   # North America / Europe
    ("premium", "2"):  0.12,    # Asia, Oceania, MEA
    ("premium", "3"):  0.181,   # South America
    ("premium", "de"): 0.087,
    # Transit ISP / Public Internet
    ("isp", "1"):  0.04,
    ("isp", "2"):  0.06,
    ("isp", "3"):  0.075,
    ("isp", "de"): 0.04,
}

# ── Linux software slug -> offer key candidates (tried in order) ──────────────
# Each entry: (slug, [candidate_offer_keys...], model)
# model: "per-core" -> append "-{N}-core" to each candidate
#        "flat"     -> use candidate key directly
# We try candidates in order, returning first match found in offers.
LINUX_OFFER_CANDIDATES = {
    # Free — no software charge
    "ubuntu":            ([], None),
    # Ubuntu Pro — per-core
    "ubuntu-pro":        (["ubuntu-pro"], "per-core"),
    # Ubuntu Advantage — flat per-hour (not per-core)
    # Azure UI defaults to Essential (Support) tier — try essential first.
    # "ubuntu-advantage-standard" is ~3x more expensive and is NOT the Azure default.
    "ubuntu-advantage":  (["ubuntu-advantage-essential", "ubuntu-advantage-standard", "ubuntu-advantage"], "flat"),
    # RHEL variants
    "redhat":            (["redhat", "rhel"], "per-core"),
    "rhel-ha":           (["rhel-ha"], "per-core"),
    "rhel-sap-business": (["rhel-sap-business", "rhel-sap-business-applications"], "per-core"),
    "rhel-sap-ha":       (["rhel-sap-ha", "rhel-sap-hana-ha"], "per-core"),
    # SUSE variants — Azure calculator uses sles-basic / sles-hpc-standard / sles-sap
    "sles-enterprise":   (["sles-basic", "sles-enterprise"], "per-core"),
    "sles-hpc":          (["sles-hpc-standard", "sles-hpc"], "per-core"),
    "sles-sap-priority": (["sles-sap", "sles-sap-priority"], "per-core"),
    # SQL Server Linux variants — EDITION matters:
    #   sql-redhat / sql-sles-priority / sql-linux → Azure UI default = Enterprise edition
    #   sql-ubuntu-pro                             → Azure UI default = Developer edition
    #     (Developer is free; charge is Ubuntu Pro per-core only)
    #
    # Try enterprise keys first for sql-redhat, sql-sles-priority, sql-linux.
    # The standard keys resolve to Standard edition prices which are ~3-4x cheaper
    # than what Azure's calculator shows for these types.
    "sql-redhat":        ([
        "sql-server-enterprise-redhat",      # most likely Azure API key form
        "sql-server-redhat-enterprise",
        "sql-server-redhat-standard",        # fallback: Standard edition
        "sql-redhat-standard",
        "sql-redhat",
    ], "per-core"),
    "sql-sles-priority": ([
        "sql-server-enterprise-sles",
        "sql-server-sles-enterprise",
        "sql-server-sles-priority-enterprise",
        "sql-server-sles-standard",          # fallback: Standard edition
        "sql-sles-priority-standard",
        "sql-sles-priority",
    ], "per-core"),
    "sql-linux":         ([
        "sql-server-enterprise-linux",
        "sql-server-linux-enterprise",
        "sql-server-linux-standard",         # fallback: Standard edition
        "sql-linux-standard",
        "sql-linux",
    ], "per-core"),
    # sql-ubuntu-pro = SQL Server Developer (free) + Ubuntu Pro (charged per-core).
    # Try developer-specific keys; fall back to ubuntu-pro alone if none found.
    "sql-ubuntu-pro":    ([
        "sql-server-developer-ubuntu-pro",
        "sql-server-ubuntu-pro-developer",
        "ubuntu-pro",                        # Developer is free; charge Ubuntu Pro only
        "sql-server-ubuntu-pro-standard",    # last-resort fallback (overcharges)
        "sql-ubuntu-pro-standard",
        "sql-ubuntu-pro",
    ], "per-core"),
}


# ── Fetchers ──────────────────────────────────────────────────────────────────

def _fetch_vm_metadata():
    return fetch_metadata(VM_METADATA_URL, "vm_metadata")

def _fetch_vm_calculator(region):
    return fetch_pricing(VM_CALCULATOR_URL.format(region=region), f"vm_calc_{region}")

def _fetch_disk_calculator():
    return fetch_pricing(DISK_CALCULATOR_URL, "disk_calc_v2")

def _fetch_bw_calculator():
    return fetch_pricing(BW_CALCULATOR_URL, "bw_calc_v2")


# ── Price helpers ─────────────────────────────────────────────────────────────

def _get_offer(offers, key):
    return offers.get(key) or {}

def _coerce_price_value(value):
    """Handle Azure v2/v4 price nodes that may be raw numbers or {value: number} objects."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        return None
    return float(value)

def _get_price_v4(offer, price_key, region):
    """v4: {prices: {perhour: {region: {value: X}}}}"""
    prices = offer.get("prices", {}).get(price_key, {})
    node = prices.get(region) or prices.get("global")
    return _coerce_price_value(node)

def _get_price_v2(offer, region):
    """v2 disk/BW: prices may be raw numbers or {value: number} objects."""
    prices = offer.get("prices", {})
    node = prices.get(region) or prices.get("global")
    return _coerce_price_value(node)

def _get_offer_cores(offers, size, tier):
    for key in (f"windows-{size}-{tier}", f"linux-{size}-{tier}"):
        cores = _get_offer(offers, key).get("cores")
        if cores is not None:
            return int(float(cores))
    return None


# ── Linux types ───────────────────────────────────────────────────────────────

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


# ── VM pricing ────────────────────────────────────────────────────────────────

def _get_compute_hourly(offers, size, tier, region):
    return _get_price_v4(_get_offer(offers, f"linux-{size}-{tier}"), "perhour", region)

def _get_windows_license_hourly(offers, size, tier, region):
    win = _get_price_v4(_get_offer(offers, f"windows-{size}-{tier}"), "perhour", region)
    lin = _get_price_v4(_get_offer(offers, f"linux-{size}-{tier}"),   "perhour", region)
    if win is not None and lin is not None:
        return max(win - lin, 0.0)
    cores = _get_offer_cores(offers, size, tier)
    if cores:
        p = _get_price_v4(_get_offer(offers, f"windows-ri-{cores}-core"), "perhour", region)
        if p is not None:
            return p
    return 0.0

def _get_software_hourly(offers, size, tier, linux_type_slug, region):
    entry = LINUX_OFFER_CANDIDATES.get(linux_type_slug)
    if not entry:
        return None
    candidates, model = entry
    if not candidates or model is None:
        return 0.0  # free

    if model == "flat":
        for prefix in candidates:
            p = _get_price_v4(_get_offer(offers, prefix), "perhour", region)
            if p is not None:
                return p
        return None

    # per-core: need core count first
    cores = _get_offer_cores(offers, size, tier)
    if not cores:
        return None
    for prefix in candidates:
        p = _get_price_v4(_get_offer(offers, f"{prefix}-{cores}-core"), "perhour", region)
        if p is not None:
            return p
    return None

def _get_vm_hourly(offers, region, size, tier, operating_system, linux_type="ubuntu", ahb=False):
    compute = _get_compute_hourly(offers, size, tier, region)
    if compute is None:
        return _get_price_v4(_get_offer(offers, f"windows-{size}-{tier}"), "perhour", region)
    if operating_system == "windows":
        return compute + (0.0 if ahb else _get_windows_license_hourly(offers, size, tier, region))
    software = _get_software_hourly(offers, size, tier, linux_type, region)
    if software is None:
        return None
    return compute + software


# ── VM Schema + Calculate ─────────────────────────────────────────────────────

def _build_vm_schema(meta, calc_data, region, operating_system="linux",
                     linux_type="ubuntu", tier="standard", selected_size=None):
    offers = calc_data.get("offers", {})

    regions      = meta.get("regions", [])
    region_enum  = [r["slug"]        for r in regions]
    region_names = [r["displayName"] for r in regions]

    tiers      = meta.get("tiers", [{"slug":"standard","displayName":"Standard"},
                                     {"slug":"basic",   "displayName":"Basic"}])
    tier_enum  = [t["slug"]        for t in tiers]
    tier_names = [t["displayName"] for t in tiers]

    linux_types      = _get_linux_types_from_metadata(meta)
    linux_type_enum  = [s for s, _ in linux_types]
    linux_type_names = [n for _, n in linux_types]
    linux_type_default = linux_type if linux_type in linux_type_enum else linux_type_enum[0]

    return {
        "type": "object",
        "title": "Azure Virtual Machines",
        "required": ["region", "operatingSystem", "tier", "instanceSelector", "count", "hours"],
        "properties": {
            "region": {
                "type": "string", "title": "Region",
                "enum": region_enum, "enumNames": region_names,
                "default": calc_data.get("schema", {}).get("region", "us-east"),
            },
            "operatingSystem": {
                "type": "string", "title": "Operating System",
                "enum": ["linux", "windows"], "enumNames": ["Linux", "Windows"],
                "default": operating_system,
            },
            "linuxType": {
                "type": "string", "title": "Linux Type",
                "enum": linux_type_enum, "enumNames": linux_type_names,
                "default": linux_type_default,
            },
            "tier": {
                "type": "string", "title": "Tier",
                "enum": tier_enum, "enumNames": tier_names,
                "default": tier,
            },
            # instanceSelector is now an object owned by the custom React field.
            # The backend only needs the slug from instanceSelector.instanceSize for pricing.
            "instanceSelector": {
                "type": "object",
                "title": "Instance",
                "properties": {
                    "category":     {"type": "string"},
                    "series":       {"type": "string"},
                    "instanceSize": {"type": "string"},
                },
            },
            "addHybridBenefit": {
                "type": "boolean", "title": "Azure Hybrid Benefit",
                "default": False,
            },
            "count": {"type": "integer", "title": "VM Count", "default": 1, "minimum": 1},
            "hours": {"type": "number", "title": "Hours / Month",
                      "default": 730, "minimum": 1, "maximum": 744},
        },
    }

def _calc_vm(fd, calc_data, region):
    os_   = fd.get("operatingSystem", "linux")
    lt_   = fd.get("linuxType", "ubuntu")
    tier_ = fd.get("tier", "standard")
    count = float(fd.get("count", 1))
    hours = float(fd.get("hours", 730))
    ahb   = fd.get("addHybridBenefit", False)

    # Support both old flat `size` key (backwards compat) and new nested instanceSelector.
    inst_sel = fd.get("instanceSelector") or {}
    size_ = (
        inst_sel.get("instanceSize")
        or fd.get("instanceSize")
        or fd.get("size", "")
    )

    if not size_:
        return None   # caller returns 400
    offers = calc_data.get("offers", {})
    price = _get_vm_hourly(offers, region, size_, tier_, os_, lt_, ahb)
    if price is None:
        return None
    return round(price * hours * count, 4)


# ── Instance Selector — MongoDB-backed endpoints ──────────────────────────────

@ns.route("/instances/categories")
class InstanceCategories(Resource):
    def get(self):
        """
        Return all distinct VM categories.
        Categories are global (not region-filtered) — every category exists
        in every major region so filtering here adds overhead with no UX benefit.
        """
        try:
            col = _get_collection()
            pipeline = [
                {"$match": {"provider": "azure"}},
                {"$group": {
                    "_id":     "$category",
                    "display": {"$first": "$category_display"},
                }},
                {"$sort": {"_id": 1}},
            ]
            docs = list(col.aggregate(pipeline))
            return {
                "categories": [
                    {"slug": d["_id"], "display": d["display"]}
                    for d in docs
                ]
            }
        except Exception as e:
            return {"error": str(e)}, 500


@ns.route("/instances/series")
class InstanceSeries(Resource):
    def get(self):
        """
        Return distinct series available in a given region + category.
        ?region=   (required for region-aware filtering)
        ?category= (required)
        Both params are required for meaningful results; falls back gracefully if omitted.
        """
        try:
            col      = _get_collection()
            region   = request.args.get("region",   "").strip()
            category = request.args.get("category", "").strip()
            match    = {"provider": "azure"}
            if region:
                match["region"] = region
            if category:
                match["category"] = category
            pipeline = [
                {"$match": match},
                {"$group": {
                    "_id":     "$series",
                    "display": {"$first": "$series_display"},
                }},
                {"$sort": {"_id": 1}},
            ]
            docs = list(col.aggregate(pipeline))
            return {
                "series": [
                    {"slug": d["_id"], "display": d["display"]}
                    for d in docs
                ]
            }
        except Exception as e:
            return {"error": str(e)}, 500


@ns.route("/instances/sizes")
class InstanceSizes(Resource):
    def get(self):
        """
        Return instance sizes for a given region + category + series.
        This is now a single document point-lookup — O(1) after index.
        ?region=   (required)
        ?category= (required)
        ?series=   (required)
        """
        try:
            col      = _get_collection()
            region   = request.args.get("region",   "").strip()
            category = request.args.get("category", "").strip()
            series   = request.args.get("series",   "").strip()

            if not region or not category or not series:
                return {"error": "region, category, and series are all required"}, 400

            doc = col.find_one(
                {
                    "provider": "azure",
                    "region":   region,
                    "category": category,
                    "series":   series,
                },
                {"_id": 0, "instances": 1},
            )
            if not doc:
                return {"sizes": []}
            return {"sizes": doc.get("instances", [])}
        except Exception as e:
            return {"error": str(e)}, 500


# ── Managed Disks ─────────────────────────────────────────────────────────────

def _get_disk_offer_key(disk_tier, size, redundancy):
    offer_prefix = DISK_TIERS.get(disk_tier, DISK_TIERS["standardssd"])[3]
    if disk_tier == "standardhdd":
        return f"{offer_prefix}-{size}"        # e.g. standardhdd-s10
    return f"{offer_prefix}-{size}-{redundancy}"  # e.g. standardssd-e10-lrs

def _get_disk_monthly(offers, region, disk_tier, size, redundancy):
    key = _get_disk_offer_key(disk_tier, size, redundancy)
    return _get_price_v2(_get_offer(offers, key), region)

def _build_disk_schema(calc_data, region, disk_tier="standardssd",
                       redundancy="lrs", disk_size="e10"):
    if disk_tier not in DISK_TIERS:
        disk_tier = "standardssd"

    tier_enum  = list(DISK_TIERS.keys())
    tier_names = [DISK_TIERS[t][0] for t in tier_enum]
    prefix     = DISK_TIERS[disk_tier][1]
    red_opts   = DISK_TIERS[disk_tier][2]
    red_names  = [r.upper() for r in red_opts]
    if redundancy not in red_opts:
        redundancy = red_opts[0]

    offers     = calc_data.get("offers", {})
    size_enum  = []
    size_names = []
    for slug, base_label in DISK_SIZES.get(prefix, []):
        price = _get_disk_monthly(offers, region, disk_tier, slug, redundancy)
        label = f"{base_label}, ${price:.3f}/month" if price is not None else base_label
        size_enum.append(slug)
        size_names.append(label)

    default_size = disk_size if disk_size in size_enum else (size_enum[0] if size_enum else "e10")

    return {
        "type": "object",
        "title": "Managed Disks",
        "required": ["diskTier", "redundancy", "diskSize", "count"],
        "properties": {
            "diskTier":   {"type":"string","title":"Tier",
                           "enum":tier_enum,"enumNames":tier_names,"default":disk_tier},
            "redundancy": {"type":"string","title":"Redundancy",
                           "enum":red_opts,"enumNames":red_names,"default":redundancy},
            "diskSize":   {"type":"string","title":"Disk Size",
                           "enum":size_enum,"enumNames":size_names,"default":default_size},
            "count":      {"type":"integer","title":"Number of Disks","default":1,"minimum":1},
        },
    }

def _calc_disk(fd, calc_data, region):
    disk_tier  = fd.get("diskTier",   "standardssd")
    redundancy = fd.get("redundancy", "lrs")
    disk_size  = fd.get("diskSize",   "e10")
    count      = float(fd.get("count", 1))
    offers     = calc_data.get("offers", {})
    price      = _get_disk_monthly(offers, region, disk_tier, disk_size, redundancy)
    if price is None:
        return 0.0
    return round(price * count, 4)


# ── Storage Transactions ──────────────────────────────────────────────────────
# Actual offer keys: "transactions-hdd"  (Standard HDD)
#                    "transactions-ssd"  (Standard SSD)
# Prices: region-keyed directly. Value = price per 10,000 transactions.

def _get_txn_price(offers, region, disk_tier):
    if disk_tier == "premiumssd":
        return None
    key = "transactions-hdd" if disk_tier == "standardhdd" else "transactions-ssd"
    return _get_price_v2(_get_offer(offers, key), region)

def _build_storage_txn_schema(disk_tier="standardssd"):
    is_premium = disk_tier == "premiumssd"
    return {
        "type": "object",
        "title": "Storage Transactions",
        "required": ["transactionUnits"],
        "properties": {
            "transactionUnits": {
                "type": "number",
                "title": "Transaction Units (10,000 transactions each)",
                "default": 0, "minimum": 0,
                "description": (
                    "Premium SSD does not incur transaction charges."
                    if is_premium else
                    "Number of units where 1 unit = 10,000 transactions."
                ),
            },
        },
    }

def _get_txn_billable_units(units, disk_tier, redundancy, disk_size, disk_count):
    if units <= 0:
        return 0.0

    redundancy = (redundancy or "lrs").lower()
    disk_size  = (disk_size or "").lower()
    disk_count = max(float(disk_count or 1), 1.0)

    if disk_tier == "standardssd":
        hourly_limit = STANDARD_SSD_TXN_LIMITS.get(redundancy, {}).get(disk_size)
        if hourly_limit:
            max_units = (hourly_limit * MONTHLY_HOURS * disk_count) / 10000.0
            return min(units, max_units)
        return units

    if disk_tier == "standardhdd":
        hourly_limit = STANDARD_HDD_TXN_LIMITS.get(disk_size)
        if hourly_limit:
            max_units = (hourly_limit * MONTHLY_HOURS * disk_count) / 10000.0
            return min(units, max_units)
        return units

    return units


def _calc_storage_txn(fd, disk_calc_data, region, disk_tier, redundancy, disk_size=None, disk_count=1):
    if disk_tier == "premiumssd":
        return 0.0
    units  = float(fd.get("transactionUnits", 0))
    offers = disk_calc_data.get("offers", {})
    price  = _get_txn_price(offers, region, disk_tier)
    if price is None:
        return 0.0
    billable_units = _get_txn_billable_units(units, disk_tier, redundancy, disk_size, disk_count)
    return round(price * billable_units, 6)


# ── Bandwidth ─────────────────────────────────────────────────────────────────

def _build_bw_schema(bw_calc_data, region):
    vm_meta, _ = _fetch_vm_metadata()
    vm_meta = vm_meta or {}
    vm_regions = vm_meta.get("regions", [])
    if vm_regions:
        region_enum  = [r["slug"]        for r in vm_regions]
        region_names = [r["displayName"] for r in vm_regions]
    else:
        region_enum  = [region]
        region_names = [region]

    return {
        "type": "object",
        "title": "Bandwidth",
        "required": ["dataTransferType", "sourceRegion", "egressGB"],
        "properties": {
            "dataTransferType": {
                "type": "string", "title": "Data Transfer Type",
                "enum":      [t for t, _ in TRANSFER_TYPES],
                "enumNames": [n for _, n in TRANSFER_TYPES],
                "default":   "interregion",
            },
            "sourceRegion": {
                "type": "string", "title": "Source Region",
                "enum": region_enum, "enumNames": region_names, "default": region,
            },
            "destinationRegion": {
                "type": "string", "title": "Destination Region",
                "enum": region_enum, "enumNames": region_names,
                "default": (
                    "asia-pacific-east" if "asia-pacific-east" in region_enum
                    else (region_enum[1] if len(region_enum) > 1 else region_enum[0])
                ),
            },
            "routedVia": {
                "type": "string", "title": "Routed Via",
                "enum":      [v for v, _ in ROUTED_VIA_OPTIONS],
                "enumNames": [n for _, n in ROUTED_VIA_OPTIONS],
                "default":   "premium",
            },
            "egressGB": {
                "type": "number", "title": "Outbound Data Transfer (GB)",
                "default": 5, "minimum": 0,
            },
        },
    }

def _calc_bw(fd, bw_calc_data, region):
    transfer_type = fd.get("dataTransferType", "interregion")
    egress_gb     = float(fd.get("egressGB", 0))
    src_region    = fd.get("sourceRegion", region)
    dest_region   = fd.get("destinationRegion", "")
    routed_via    = fd.get("routedVia", "premium")   # "premium" | "isp"
    offers        = bw_calc_data.get("offers", {})

    src_zone  = REGION_TO_ZONE.get(src_region, "1")
    dest_zone = REGION_TO_ZONE.get(dest_region, "2")

    def _try_offer(keys):
        """Try a list of offer keys; return TOTAL cost (price_per_gb * egress_gb) or None."""
        for key in keys:
            offer = _get_offer(offers, key)
            if not offer:
                continue
            tiers = offer.get("tiers")
            if isinstance(tiers, list) and tiers:
                per_gb = get_graduated_price(tiers, egress_gb)
                if per_gb is not None:
                    return per_gb * egress_gb
            p = _get_price_v2(offer, src_region)
            if p is not None:
                return p * egress_gb
            for pk in ("pergb", "perunit"):
                p = _get_price_v4(offer, pk, src_region)
                if p is not None:
                    return p * egress_gb
        return None

    if transfer_type == "internetegress":
        if routed_via == "isp":
            offer_keys = [
                f"isp-egress-zone{src_zone}",
                f"transit-egress-zone{src_zone}",
                f"internet-isp-egress-zone{src_zone}",
            ]
        else:
            offer_keys = [
                f"internet-egress-zone{src_zone}",
                f"egress-zone{src_zone}",
                f"internet-egress-{src_region}",
                "internet-egress",
                "egress",
            ]
        price = _try_offer(offer_keys)

        if price is None:
            FREE_GB  = 100.0
            billable = max(egress_gb - FREE_GB, 0.0)
            if billable == 0.0:
                return 0.0
            rate  = INTERNET_EGRESS_RATES.get((routed_via, src_zone),
                    INTERNET_EGRESS_RATES.get(("premium", src_zone), 0.087))
            price = rate * billable

    else:
        price = _try_offer([
            f"interregion-zone{src_zone}-zone{dest_zone}",
            f"interregion-zone{dest_zone}-zone{src_zone}",
            f"interregion-{src_region}-{dest_region}",
            f"interregion-{dest_region}-{src_region}",
            f"interregion-zone{src_zone}",
            "interregion",
        ])
        if price is None:
            FREE_GB  = 5.0
            billable = max(egress_gb - FREE_GB, 0.0)
            if billable == 0.0:
                return 0.0
            CONTINENT = {
                "1":  "north_america_europe",
                "2":  "asia_oceania",
                "3":  "south_america_mea",
                "de": "north_america_europe",
            }
            src_cont  = CONTINENT.get(src_zone,  "north_america_europe")
            dest_cont = CONTINENT.get(dest_zone, "asia_oceania")
            if src_cont == dest_cont:
                INTRA_RATE = {
                    "north_america_europe": 0.02,
                    "asia_oceania":         0.08,
                    "south_america_mea":    0.16,
                }
                rate = INTRA_RATE.get(src_cont, 0.05)
            else:
                INTER_RATE = {
                    "north_america_europe": 0.05,
                    "asia_oceania":         0.08,
                    "south_america_mea":    0.16,
                }
                rate = INTER_RATE.get(src_cont, 0.05)
            price = rate * billable

    return round(price, 4) if price is not None else 0.0


# ── Debug endpoints ───────────────────────────────────────────────────────────

@ns.route("/debug/software-offers")
class SoftwareOfferDebug(Resource):
    def get(self):
        region = request.args.get("region", "us-east")
        calc, err = _fetch_vm_calculator(region)
        if err:
            return {"error": err}, 502
        offers = calc.get("offers", {})
        prefixes = ["ubuntu-pro", "ubuntu-advantage", "redhat", "rhel", "sles",
                    "sql-server", "sql-linux", "sql-redhat", "sql-sles", "sql-ubuntu"]
        result = {}
        for key in offers:
            for p in prefixes:
                if key.startswith(p):
                    result[key] = True
                    break
        return {"count": len(result), "keys": sorted(result.keys())}


@ns.route("/debug/bandwidth-offers")
class BandwidthOfferDebug(Resource):
    def get(self):
        """Inspect actual offer keys returned by the v2 bandwidth API."""
        calc, err = _fetch_bw_calculator()
        if err:
            return {"error": err}, 502
        offers = calc.get("offers", {})
        sample = {}
        for key, val in list(offers.items())[:50]:
            prices = val.get("prices", {})
            sample[key] = {
                "price_keys": list(prices.keys())[:5],
                "sample_price": list(prices.values())[0] if prices else None,
            }
        return {
            "offer_count": len(offers),
            "all_keys": sorted(offers.keys()),
            "sample": sample,
        }


# ── Route handlers ────────────────────────────────────────────────────────────

@ns.route("/schema")
class Schema(Resource):
    def get(self):
        region   = request.args.get("region", "us-east")
        os_      = request.args.get("operatingSystem", "linux")
        lt_      = request.args.get("linuxType", "ubuntu")
        tier     = request.args.get("tier", "standard")

        meta, err = _fetch_vm_metadata()
        if err:
            return {"error": f"Azure metadata error: {err}"}, 502
        calc, err = _fetch_vm_calculator(region)
        if err:
            return {"error": f"Azure calculator error: {err}"}, 502

        schema = _build_vm_schema(meta, calc, region, os_, lt_, tier)
        props  = schema["properties"]
        return {"schema": schema, "defaults": {k: v["default"] for k, v in props.items() if "default" in v}}


calculate_model = ns.model("VMCalculate", {
    "region": fields.String(required=True), "form_data": fields.Raw(required=True),
})

@ns.route("/calculate")
class Calculate(Resource):
    @ns.expect(calculate_model)
    def post(self):
        body = ns.payload or {}
        region, fd = body.get("region", "us-east"), body.get("form_data", {})
        if not region or not fd:
            return {"error": "region and form_data are required"}, 400
        calc, err = _fetch_vm_calculator(region)
        if err:
            return {"error": f"Azure calculator error: {err}"}, 502
        total = _calc_vm(fd, calc, region)
        if total is None:
            return {"error": "Selected VM size is not available for the chosen OS/subtype/tier in this region"}, 400
        return {"monthly_total": total, "currency": "USD", "region": region}


# ── Managed Disks ─────────────────────────────────────────────────────────────

@ns.route("/managed-disks/schema")
class DiskSchema(Resource):
    def get(self):
        region     = request.args.get("region", "us-east")
        disk_tier  = request.args.get("diskTier", "standardssd")
        redundancy = request.args.get("redundancy", "lrs")
        disk_size  = request.args.get("diskSize", "e10")

        calc, err = _fetch_disk_calculator()
        if err:
            return {"error": f"Azure disk pricing error: {err}"}, 502

        schema = _build_disk_schema(calc, region, disk_tier, redundancy, disk_size)
        return {"schema": schema, "defaults": {k: v["default"] for k, v in schema["properties"].items() if "default" in v}}


disk_calc_model = ns.model("DiskCalc", {
    "region": fields.String(required=True), "form_data": fields.Raw(required=True),
})

@ns.route("/managed-disks/calculate")
class DiskCalculate(Resource):
    @ns.expect(disk_calc_model)
    def post(self):
        body = ns.payload or {}
        region, fd = body.get("region", "us-east"), body.get("form_data", {})
        calc, err = _fetch_disk_calculator()
        if err:
            return {"error": f"Azure disk pricing error: {err}"}, 502
        return {"monthly_total": _calc_disk(fd, calc, region), "currency": "USD", "region": region}


# ── Storage Transactions ──────────────────────────────────────────────────────

@ns.route("/storage-transactions/schema")
class TxnSchema(Resource):
    def get(self):
        disk_tier = request.args.get("diskTier", "standardssd")
        schema    = _build_storage_txn_schema(disk_tier)
        return {"schema": schema, "defaults": {k: v["default"] for k, v in schema["properties"].items() if "default" in v}}


txn_calc_model = ns.model("TxnCalc", {
    "region": fields.String(required=True), "disk_tier": fields.String(),
    "redundancy": fields.String(), "disk_size": fields.String(),
    "disk_count": fields.Float(), "form_data": fields.Raw(required=True),
})

@ns.route("/storage-transactions/calculate")
class TxnCalculate(Resource):
    @ns.expect(txn_calc_model)
    def post(self):
        body       = ns.payload or {}
        region     = body.get("region", "us-east")
        disk_tier  = body.get("disk_tier", "standardssd")
        redundancy = body.get("redundancy", "lrs")
        disk_size  = body.get("disk_size")
        disk_count = body.get("disk_count", 1)
        fd         = body.get("form_data", {})
        calc, err  = _fetch_disk_calculator()
        if err:
            return {"error": f"Azure disk pricing error: {err}"}, 502
        return {
            "monthly_total": _calc_storage_txn(fd, calc, region, disk_tier, redundancy, disk_size, disk_count),
            "currency": "USD",
            "region": region,
        }


# ── Bandwidth ─────────────────────────────────────────────────────────────────

@ns.route("/bandwidth/schema")
class BwSchema(Resource):
    def get(self):
        region    = request.args.get("region", "us-east")
        calc, err = _fetch_bw_calculator()
        if err:
            return {"error": f"Azure bandwidth pricing error: {err}"}, 502
        schema = _build_bw_schema(calc, region)
        return {"schema": schema, "defaults": {k: v["default"] for k, v in schema["properties"].items() if "default" in v}}


bw_calc_model = ns.model("BwCalc", {
    "region": fields.String(required=True), "form_data": fields.Raw(required=True),
})

@ns.route("/bandwidth/calculate")
class BwCalculate(Resource):
    @ns.expect(bw_calc_model)
    def post(self):
        body    = ns.payload or {}
        region  = body.get("region", "us-east")
        fd      = body.get("form_data", {})
        calc, err = _fetch_bw_calculator()
        if err:
            return {"error": f"Azure bandwidth pricing error: {err}"}, 502
        return {"monthly_total": _calc_bw(fd, calc, region), "currency": "USD", "region": region}