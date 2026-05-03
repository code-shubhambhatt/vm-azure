"""
storage.py — Azure Storage Accounts pricing service
Endpoints: /api/storage/*

Architecture: metadata-driven
  - On every /schema request, fetch_pricing() is called (result is cached for 1hr by shared.py)
  - All dropdown options (regions, redundancies, tiers, namespaces) are derived from which
    offer keys actually exist with real prices in the metadata.
  - No hardcoded option lists. If Azure adds a new region, redundancy, or tier variant, it
    appears automatically without code changes.
  - Label mappings (slug → human label) are still code-side because the metadata JSON
    carries no human-readable labels.
"""

from flask import request
from flask_restx import Namespace, Resource, fields
from .shared import fetch_pricing, get_graduated_price

ns = Namespace("storage", description="Azure Storage Accounts pricing")

STORAGE_URL   = "https://azure.microsoft.com/api/v3/pricing/storage/calculator/?culture=en-us"
MONTHLY_HOURS = 730.0

# ════════════════════════════════════════════════════════════════════════════════
# LABEL MAPS — slug → human label (metadata has no labels, these stay as code)
# ════════════════════════════════════════════════════════════════════════════════

# Ordered so UI dropdowns appear in a sensible sequence
_REDUND_ORDER  = ["lrs", "zrs", "grs", "ra-grs", "gzrs", "ra-gzrs"]
_REDUND_LABELS = {
    "lrs": "LRS", "zrs": "ZRS", "grs": "GRS",
    "ra-grs": "RA-GRS", "gzrs": "GZRS", "ra-gzrs": "RA-GZRS",
}

_TIER_ORDER  = ["hot", "cool", "cold", "archive", "transactionoptimized", "premium"]
_TIER_LABELS = {
    "hot": "Hot", "cool": "Cool", "cold": "Cold", "archive": "Archive",
    "transactionoptimized": "Transaction Optimized", "premium": "Premium",
}

# Per-type tier orders — each type only sees tiers that make sense for it.
# _derive_options will further filter to only those with real metadata keys.
_BLOB_TIER_ORDER = ["hot", "cool", "cold", "archive"]        # block blob + blob storage
_DL_TIER_ORDER   = ["hot", "cool", "cold", "archive", "premium"]  # data lake (premium = perf tier, flat ns only)
_FILE_TIER_ORDER = ["transactionoptimized", "hot", "cool"]   # standard files

_NS_LABELS   = {"flat": "Flat Namespace", "structured": "Hierarchical Namespace"}
_PERF_LABELS = {"standard": "Standard", "premium": "Premium (SSD)"}
_DISK_LABELS = {"hdd": "HDD (Standard)", "ssd": "SSD (Premium)"}

_ACCT_BLOB_ORDER  = ["gpv2", "gpv1", "blob"]
_ACCT_BLOB_LABELS = {
    "gpv2": "General Purpose V2",
    "gpv1": "General Purpose V1 (Legacy)",
    "blob": "Blob Storage (Legacy)",
}

_ACCT_QUEUE_ORDER  = ["gpv2", "gpv1"]
_ACCT_QUEUE_LABELS = {
    "gpv2": "General Purpose V2",
    "gpv1": "General Purpose V1 (Legacy)",
}

_TABLE_TIER_ORDER  = ["standard", "account-encrypted"]
_TABLE_TIER_LABELS = {"standard": "Standard", "account-encrypted": "Account Encrypted"}

# Known region slug → label (used to convert metadata keys to display names)
_KNOWN_REGIONS = {
    "asia-pacific-east":      "East Asia",
    "asia-pacific-southeast": "Southeast Asia",
    "australia-central":      "Australia Central",
    "australia-central-2":    "Australia Central 2",
    "australia-east":         "Australia East",
    "australia-southeast":    "Australia Southeast",
    "brazil-south":           "Brazil South",
    "brazil-southeast":       "Brazil Southeast",
    "canada-central":         "Canada Central",
    "canada-east":            "Canada East",
    "central-india":          "Central India",
    "south-india":            "South India",
    "west-india":             "West India",
    "europe-north":           "North Europe",
    "europe-west":            "West Europe",
    "france-central":         "France Central",
    "france-south":           "France South",
    "germany-north":          "Germany North",
    "germany-west-central":   "Germany West Central",
    "israel-central":         "Israel Central",
    "italy-north":            "Italy North",
    "japan-east":             "Japan East",
    "japan-west":             "Japan West",
    "korea-central":          "Korea Central",
    "korea-south":            "Korea South",
    "mexico-central":         "Mexico Central",
    "norway-east":            "Norway East",
    "norway-west":            "Norway West",
    "poland-central":         "Poland Central",
    "qatar-central":          "Qatar Central",
    "south-africa-north":     "South Africa North",
    "south-africa-west":      "South Africa West",
    "spain-central":          "Spain Central",
    "sweden-central":         "Sweden Central",
    "sweden-south":           "Sweden South",
    "switzerland-north":      "Switzerland North",
    "switzerland-west":       "Switzerland West",
    "uae-central":            "UAE Central",
    "uae-north":              "UAE North",
    "united-kingdom-south":   "UK South",
    "united-kingdom-west":    "UK West",
    "us-central":             "Central US",
    "us-east":                "East US",
    "us-east-2":              "East US 2",
    "us-north-central":       "North Central US",
    "us-south-central":       "South Central US",
    "us-west-central":        "West Central US",
    "us-west":                "West US",
    "us-west-2":              "West US 2",
    "us-west-3":              "West US 3",
    "usgov-arizona":          "US Gov Arizona",
    "usgov-texas":            "US Gov Texas",
    "usgov-virginia":         "US Gov Virginia",
}

# Sets used in calc logic (derived from label maps, not hardcoded separately)
_GEO_REDUND  = {"grs", "ra-grs", "gzrs", "ra-gzrs"}

def _fetch():
    return fetch_pricing(STORAGE_URL, "storage-v3")


# ════════════════════════════════════════════════════════════════════════════════
# METADATA DERIVATION HELPERS
# All "what options are available?" questions are answered by checking the metadata.
# ════════════════════════════════════════════════════════════════════════════════

def _has_price(offers, key):
    """True if key exists in offers AND has at least one non-null regional price.

    Checks both price structures Azure uses:
    - 'prices'          → flat per-unit pricing (ops, metadata, geo transfer etc.)
    - 'graduatedPrices' → tiered GB pricing used for ALL capacity keys (block blob,
                          data lake, files etc.). If we only check 'prices', every
                          capacity-based tier detection call returns False, breaking
                          the entire metadata-driven option derivation for those types.
    """
    o = offers.get(key)
    if not o:
        return False
    # Flat prices
    for unit_dict in o.get("prices", {}).values():
        for entry in unit_dict.values():
            if isinstance(entry, dict) and entry.get("value") is not None:
                return True
    # Graduated (tiered) prices — capacity keys
    for unit_dict in o.get("graduatedPrices", {}).values():
        for reg_dict in unit_dict.values():
            if isinstance(reg_dict, dict) and reg_dict.get("prices"):
                return True
    return False

def _derive_regions(offers):
    """
    Return [(slug, label)] for every region that appears in at least one offer price.
    Ordered by _KNOWN_REGIONS insertion order, unknown slugs appended sorted at end.
    """
    found = set()
    for o in offers.values():
        for unit_dict in o.get("prices", {}).values():
            found.update(unit_dict.keys())
        for unit_dict in o.get("graduatedPrices", {}).values():
            for reg_dict in unit_dict.values() if isinstance(unit_dict, dict) else []:
                if isinstance(reg_dict, dict):
                    found.update(reg_dict.keys())
    result = [(s, _KNOWN_REGIONS[s]) for s in _KNOWN_REGIONS if s in found]
    unknown = sorted(found - set(_KNOWN_REGIONS))
    result += [(s, s) for s in unknown]
    return result

def _derive_options(offers, key_fn, slug_order, label_map):
    """
    Return [(slug, label)] for each slug where key_fn(slug) has a real price.
    slug_order controls display sequence; only slugs in label_map are considered.
    """
    return [
        (s, label_map[s])
        for s in slug_order
        if s in label_map and _has_price(offers, key_fn(s))
    ]

def _first_valid(options, preferred):
    """Return preferred slug if it's in options, else first slug, else None."""
    slugs = [s for s, _ in options]
    return preferred if preferred in slugs else (slugs[0] if slugs else None)


def _has_any_priced_key(offers, keys):
    """True if any candidate key has a real price."""
    return any(_has_price(offers, key) for key in keys)


def _block_blob_base_key(offers, account_type, namespace, access_tier, redundancy, performance="standard"):
    """Azure's block blob capacity key for the selected combination.

    One Azure-specific edge case matters here:
    - Standard GPv2 + Flat Namespace + Cold tier uses the `-recommended` keys
      in the calculator. Those keys also unlock the ZRS/GZRS variants that are
      otherwise invisible if we only inspect the plain keys.
    """
    if performance == "premium":
        return f"premium-block-blob-{namespace}-{redundancy}"
    if account_type == "gpv1":
        return f"general-purpose-block-blob-{redundancy}"

    prefix = "blob-storage" if account_type == "blob" else "general-purpose-v2"
    base = f"{prefix}-block-blob-{namespace}-{access_tier}-{redundancy}"
    if account_type == "gpv2" and namespace == "flat" and access_tier == "cold":
        recommended = f"{base}-recommended"
        if _has_price(offers, recommended):
            return recommended
    return base


def _block_blob_meter_key(offers, account_type, namespace, access_tier, redundancy, meter, performance="standard"):
    """Azure's meter key for block blob operations/transfer/retrieval meters."""
    if performance == "premium":
        return f"premium-block-blob-{namespace}-{redundancy}-{meter}"
    if account_type == "gpv1":
        return f"general-purpose-block-blob-{redundancy}-{meter}"

    prefix = "blob-storage" if account_type == "blob" else "general-purpose-v2"
    key = f"{prefix}-block-blob-{namespace}-{access_tier}-{redundancy}-{meter}"
    if account_type == "gpv2" and namespace == "flat" and access_tier == "cold":
        recommended = f"{key}-recommended"
        if _has_price(offers, recommended):
            return recommended
    return key


def _block_blob_tier_options(offers, account_type):
    """Tier choices are derived across all namespaces/redundancies for the account type.

    This mirrors Azure's selector behaviour better than tying tiers to the current
    namespace. Example: Blob Storage supports the Cold tier only for Structured
    namespace, so Cold should remain selectable and then narrow the namespace list.
    """
    if account_type == "gpv1":
        return [("hot", "Hot"), ("cool", "Cool")]

    ns_candidates = ["flat", "structured"]
    return [
        (tier, _TIER_LABELS[tier])
        for tier in _BLOB_TIER_ORDER
        if _has_any_priced_key(
            offers,
            [
                _block_blob_base_key(offers, account_type, ns, tier, redund)
                for ns in ns_candidates
                for redund in _REDUND_ORDER
            ],
        )
    ]


def _block_blob_namespace_options(offers, account_type, access_tier):
    if account_type == "gpv1":
        return [("flat", "Flat Namespace")]
    return [
        (ns, _NS_LABELS[ns])
        for ns in ["flat", "structured"]
        if _has_any_priced_key(
            offers,
            [
                _block_blob_base_key(offers, account_type, ns, access_tier, redund)
                for redund in _REDUND_ORDER
            ],
        )
    ]


def _block_blob_geo_key(account_type, access_tier, redundancy, performance="standard", namespace="flat"):
    if performance == "premium" or redundancy not in _GEO_REDUND:
        return None
    if account_type == "gpv1":
        return f"general-purpose-block-blob-{redundancy}-data-transfer"
    prefix = "blob-storage" if account_type == "blob" else "general-purpose-v2"
    # Azure's calculator always prices geo-replication from the flat-namespace key.
    return f"{prefix}-block-blob-flat-{access_tier}-{redundancy}-data-transfer"


def _data_lake_metadata_key(performance, redundancy):
    """Azure prices Data Lake metadata from the hot metadata meter.

    Premium Data Lake still uses the hot metadata key in the calculator UI, and
    metadata is only surfaced for Hierarchical Namespace scenarios.
    """
    _ = performance  # kept for call-site readability
    return f"general-purpose-v2-data-lake-metadata-hot-{redundancy}"


# ════════════════════════════════════════════════════════════════════════════════
# PRICE READERS
# ════════════════════════════════════════════════════════════════════════════════

def _flat(offers, key, region):
    o = offers.get(key)
    if not o or "prices" not in o:
        return 0.0
    for unit_dict in o["prices"].values():
        entry = unit_dict.get(region)
        if isinstance(entry, dict):
            v = entry.get("value")
            if v is not None:
                return float(v)
    return 0.0

def _flat_unit(offers, key, region, unit):
    """Read a specific price unit (e.g. 'pergb') from a flat-priced offer key."""
    o = offers.get(key)
    if not o:
        return 0.0
    entry = o.get("prices", {}).get(unit, {}).get(region)
    if isinstance(entry, dict):
        v = entry.get("value")
        return float(v) if v is not None else 0.0
    return 0.0

def _grad(offers, key, region, qty):
    if qty <= 0:
        return 0.0
    o = offers.get(key)
    if not o or "graduatedPrices" not in o:
        return 0.0
    for _unit, reg_dict in o["graduatedPrices"].items():
        raw_tiers = reg_dict.get(region, {}).get("prices")
        if raw_tiers:
            return get_graduated_price(
                [{"limit": t["limit"], "price": t["price"]} for t in raw_tiers], qty
            )
    return 0.0

def _capacity(offers, key, region, qty):
    """
    Capacity pricing for block blob / data lake / files keys.

    Azure uses two structures depending on access tier:
    - Hot tier:               graduatedPrices['pergb']  (tiered volume pricing)
    - Cool / Cold / Archive:  prices['pergb']           (flat per-GB)

    _grad() returns 0 for cool/cold/archive since they have no graduatedPrices.
    _flat() returns the wrong unit for hot (reservation prices appear first).
    This helper tries graduated first, then falls back to the specific 'pergb' flat unit.
    """
    val = _grad(offers, key, region, qty)
    if val > 0:
        return val
    return qty * _flat_unit(offers, key, region, "pergb")


# ════════════════════════════════════════════════════════════════════════════════
# SCHEMA FIELD HELPERS
# ════════════════════════════════════════════════════════════════════════════════

def _region_field(regions, default="us-east"):
    slugs = [r[0] for r in regions]
    names = [r[1] for r in regions]
    d = default if default in slugs else (slugs[0] if slugs else "us-east")
    return {"type": "string", "title": "Region", "enum": slugs, "enumNames": names, "default": d}

def _enum_field(title, options, default):
    slugs = [o[0] for o in options]
    names = [o[1] for o in options]
    d = default if default in slugs else (slugs[0] if slugs else "")
    return {
        "type": "string", "title": title,
        "enum": slugs, "enumNames": names,
        "oneOf": [{"const": s, "title": n} for s, n in options],
        "default": d,
    }

def _num_field(title, default=0, desc=None):
    f = {"type": "number", "title": title, "minimum": 0, "default": default}
    if desc:
        f["description"] = desc
    return f

def _ops_field(title):
    return _num_field(title, 0, "Units of 10,000 ops (e.g. 10 = 100,000 ops/month)")

def _gb_field(title, desc=None):
    return _num_field(title, 0, desc)

def _defaults(props):
    return {k: v["default"] for k, v in props.items()}


# ════════════════════════════════════════════════════════════════════════════════
# TABLE STORAGE
#
# Key patterns (verified against metadata):
#   Standard capacity:          general-purpose-table-{redund}            (pergb, flat)
#   Standard transactions:      bare "transactions" key                   (per10k, flat)
#   Account Encrypted capacity: general-purpose-table-account-encrypted-{redund} (pergb, flat)
#   Account Encrypted ops:      general-purpose-table-{redund}-{op}       (no dedicated enc keys)
#     write=$0.025, read=$0.005, list=$0.09, batch-write=$0.075, delete=$0.00
#     scan → maps to READ-OPERATIONS rate (not scan-ops rate) — Azure confirmed
#     other-meta → NOT shown/charged for account-encrypted (Azure omits this field)
#
# Standard = simple model (2 fields). Account Encrypted = detailed ops model (6 fields).
# ════════════════════════════════════════════════════════════════════════════════

def _build_table_schema(offers, regions, region, tier, redundancy):
    redund_opts = _derive_options(
        offers,
        lambda r: f"general-purpose-table-{r}",
        _REDUND_ORDER, _REDUND_LABELS,
    )
    redundancy = _first_valid(redund_opts, redundancy)

    props = {
        "region":     _region_field(regions, region),
        "tier":       _enum_field("Tier", [(s, _TABLE_TIER_LABELS[s]) for s in _TABLE_TIER_ORDER], tier),
        "redundancy": _enum_field("Redundancy", redund_opts, redundancy),
        "storageGB":  _num_field("Storage (GB/month)", 1),
    }

    if tier == "standard":
        # Standard: single "Storage Transactions" meter (bare 'transactions' key)
        props["transactions"] = _num_field(
            "Storage Transactions (× 10,000/month)", 0,
            "Each unit = 10,000 transactions",
        )
    else:  # account-encrypted
        # Encrypted: individual ops; scan uses READ-OPERATIONS rate (not scan rate).
        # Azure does not expose Other Operations and Metadata Meters for this tier.
        props["writeOps"]  = _ops_field("Write Operations (× 10,000/month)")
        props["readOps"]   = _ops_field("Read Operations (× 10,000/month)")
        props["listOps"]   = _ops_field("List Operations (× 10,000/month)")
        props["batchOps"]  = _ops_field("Batch Write Operations (× 10,000/month)")
        props["scanOps"]   = _ops_field("Scan Operations (× 10,000/month)")
        props["deleteOps"] = _ops_field("Delete Operations (× 10,000/month — free)")

    return {"type": "object", "title": "Table Storage", "properties": props}, _defaults(props)


def _calc_table(fd, offers, region):
    tier   = fd.get("tier",       "standard")
    redund = fd.get("redundancy", "lrs")
    gb     = float(fd.get("storageGB", 0) or 0)

    cap_key  = f"general-purpose-table-account-encrypted-{redund}" \
               if tier == "account-encrypted" else f"general-purpose-table-{redund}"

    breakdown = {"capacity": round(gb * _flat(offers, cap_key, region), 4)}

    if tier == "standard":
        tx = float(fd.get("transactions", 0) or 0)
        breakdown["transactions"] = round(tx * _flat(offers, "transactions", region), 4)
    else:
        ops_base = f"general-purpose-table-{redund}"
        # scan maps to read-operations rate for account-encrypted (Azure confirmed)
        breakdown.update({
            "write_operations":       round(float(fd.get("writeOps",  0) or 0) * _flat(offers, f"{ops_base}-write-operations",       region), 4),
            "read_operations":        round(float(fd.get("readOps",   0) or 0) * _flat(offers, f"{ops_base}-read-operations",        region), 4),
            "list_operations":        round(float(fd.get("listOps",   0) or 0) * _flat(offers, f"{ops_base}-list-operations",        region), 4),
            "batch_write_operations": round(float(fd.get("batchOps",  0) or 0) * _flat(offers, f"{ops_base}-batch-write-operations", region), 4),
            "scan_operations":        round(float(fd.get("scanOps",   0) or 0) * _flat(offers, f"{ops_base}-read-operations",        region), 4),
            # delete always $0.00 — omitted from total
            # other-meta not charged for account-encrypted — omitted
        })

    return breakdown


# ════════════════════════════════════════════════════════════════════════════════
# QUEUE STORAGE
#
# Key patterns:
#   GPv1 capacity: general-purpose-queue-{redund}
#   GPv2 capacity: general-purpose-v2-queue-{redund}
#   Ops:           {base}-write-operations / {base}-read-operations
#   Geo transfer:  {base}-data-transfer
#     — GPv1: no geo key exists
#     — GPv2 GRS/GZRS: geo IS shown AND charged
#     — GPv2 RA-GRS/RA-GZRS: geo IS shown as a field but Azure does NOT charge it
#       (field is displayed for informational purposes only — excluded from total)
# ════════════════════════════════════════════════════════════════════════════════

_QUEUE_GEO_CHARGED = {"grs", "gzrs"}       # geo charged for these only
_QUEUE_GEO_SHOWN   = {"grs", "gzrs", "ra-grs", "ra-gzrs"}  # field shown for all geo redunds

def _build_queue_schema(offers, regions, region, accountType, redundancy):
    acct_opts = [(s, _ACCT_QUEUE_LABELS[s]) for s in _ACCT_QUEUE_ORDER]

    if accountType == "gpv1":
        # Derive GPv1-valid redundancies from metadata keys that actually exist
        redund_opts = _derive_options(
            offers,
            lambda r: f"general-purpose-queue-{r}",
            _REDUND_ORDER, _REDUND_LABELS,
        )
        has_geo = False  # no data-transfer keys exist for GPv1 queue
    else:  # gpv2
        redund_opts = _derive_options(
            offers,
            lambda r: f"general-purpose-v2-queue-{r}",
            _REDUND_ORDER, _REDUND_LABELS,
        )
        redundancy = _first_valid(redund_opts, redundancy)
        has_geo = redundancy in _QUEUE_GEO_SHOWN  # show field for all geo redunds

    redundancy = _first_valid(redund_opts, redundancy)

    props = {
        "region":      _region_field(regions, region),
        "accountType": _enum_field("Storage Account Type", acct_opts, accountType),
        "redundancy":  _enum_field("Redundancy", redund_opts, redundancy),
        "storageGB":   _num_field("Storage (GB/month)", 1),
        "writeOps":    _ops_field("Write Operations (× 10,000/month)"),
        "readOps":     _ops_field("Read Operations (× 10,000/month)"),
    }
    if has_geo:
        props["geoReplicationGB"] = _gb_field(
            "Geo-Replication Data Transfer (GB/month)",
            "GRS/GZRS: charged. RA-GRS/RA-GZRS: shown for reference only — not charged by Azure.",
        )
    return {"type": "object", "title": "Queue Storage", "properties": props}, _defaults(props)


def _calc_queue(fd, offers, region):
    acct_type = fd.get("accountType", "gpv2")
    redund    = fd.get("redundancy",  "lrs")
    gb        = float(fd.get("storageGB",        0) or 0)
    write     = float(fd.get("writeOps",         0) or 0)
    read      = float(fd.get("readOps",          0) or 0)

    if acct_type == "gpv1":
        base    = f"general-purpose-queue-{redund}"
        has_geo = False
    else:
        base    = f"general-purpose-v2-queue-{redund}"
        has_geo = redund in _QUEUE_GEO_CHARGED  # RA-GRS/RA-GZRS shown but NOT charged

    geo_gb = float(fd.get("geoReplicationGB", 0) or 0) if has_geo else 0.0
    return {
        "capacity":         round(gb     * _flat(offers, base,                       region), 4),
        "write_operations": round(write  * _flat(offers, f"{base}-write-operations", region), 4),
        "read_operations":  round(read   * _flat(offers, f"{base}-read-operations",  region), 4),
        "geo_replication":  round(geo_gb * _flat(offers, f"{base}-data-transfer",    region), 4),
    }


# ════════════════════════════════════════════════════════════════════════════════
# BLOCK BLOB — GPv1, GPv2, Blob Storage + Premium SSD (inline)
#
# Key patterns:
#   GPv1:    general-purpose-block-blob-{redund}
#   GPv2:    general-purpose-v2-block-blob-{namespace}-{tier}-{redund}
#   Blob:    blob-storage-block-blob-{namespace}-{tier}-{redund}
#   Premium: premium-block-blob-{namespace}-{redund}
#
#   Geo key always uses flat-namespace variant — structured keys have no prices in metadata.
# ════════════════════════════════════════════════════════════════════════════════

def _build_block_blob_schema(offers, regions, region, accountType, namespace, accessTier, redundancy, performance="standard"):
    acct_opts = [(s, _ACCT_BLOB_LABELS[s]) for s in _ACCT_BLOB_ORDER]

    # ── Premium SSD ───────────────────────────────────────────────────────────
    if performance == "premium":
        redund_opts = _derive_options(
            offers,
            lambda r: f"premium-block-blob-flat-{r}",
            _REDUND_ORDER, _REDUND_LABELS,
        )
        ns_opts = _derive_options(
            offers,
            lambda ns: f"premium-block-blob-{ns}-lrs",
            ["flat", "structured"], _NS_LABELS,
        )
        namespace  = _first_valid(ns_opts, namespace)
        redundancy = _first_valid(redund_opts, redundancy)
        props = {
            "region":      _region_field(regions, region),
            "performance": _enum_field("Performance", [("standard", "Standard"), ("premium", "Premium (SSD)")], "premium"),
            "namespace":   _enum_field("File Structure", ns_opts, namespace),
            "redundancy":  _enum_field("Redundancy", redund_opts, redundancy),
            "storageGB":   _num_field("Storage (GB/month)", 1),
            "writeOps":    _ops_field("Write Operations (× 10,000/month)"),
            "readOps":     _ops_field("Read Operations (× 10,000/month)"),
            "createOps":   _ops_field("Create / Delete Operations (× 10,000/month)"),
            "otherOps":    _ops_field("Other Operations (× 10,000/month)"),
        }
        return {"type": "object", "title": "Block Blob Storage", "properties": props}, _defaults(props)

    # ── Standard — derive all options from metadata ───────────────────────────
    if accountType == "gpv1":
        ns_opts     = [("flat", "Flat Namespace")]
        tier_opts   = _block_blob_tier_options(offers, accountType)
        accessTier  = accessTier if accessTier in ("hot", "cool") else "hot"
        redund_opts = _derive_options(
            offers,
            lambda r: _block_blob_base_key(offers, accountType, "flat", accessTier, r),
            _REDUND_ORDER, _REDUND_LABELS,
        )
    else:
        tier_opts = _block_blob_tier_options(offers, accountType)
        accessTier = _first_valid(tier_opts, accessTier)
        ns_opts = _block_blob_namespace_options(offers, accountType, accessTier)
        namespace = _first_valid(ns_opts, namespace)
        redund_opts = _derive_options(
            offers,
            lambda r: _block_blob_base_key(offers, accountType, namespace, accessTier, r),
            _REDUND_ORDER, _REDUND_LABELS,
        )

    redundancy    = _first_valid(redund_opts, redundancy)
    base          = _block_blob_base_key(offers, accountType, namespace, accessTier, redundancy)
    has_retrieval = accessTier in ("cool", "cold", "archive")
    is_archive    = accessTier == "archive"
    is_structured = namespace == "structured" and accountType in ("gpv2", "blob")
    has_create    = _has_price(offers, _block_blob_meter_key(offers, accountType, namespace, accessTier, redundancy, "create-operations")) and not is_structured
    has_iter_w    = is_structured and _has_price(offers, _block_blob_meter_key(offers, accountType, namespace, accessTier, redundancy, "iterative-write-operations"))
    has_iter_r    = is_structured and _has_price(offers, _block_blob_meter_key(offers, accountType, namespace, accessTier, redundancy, "iterative-read-operations"))
    has_other     = namespace == "flat" and _has_price(offers, _block_blob_meter_key(offers, accountType, namespace, accessTier, redundancy, "other-operations"))
    has_priority  = accountType == "gpv2" and is_archive and _has_price(offers, _block_blob_meter_key(offers, accountType, namespace, accessTier, redundancy, "priority-read-operations"))
    has_geo       = _block_blob_geo_key(accountType, accessTier, redundancy, performance, namespace) is not None

    props = {
        "region":      _region_field(regions, region),
        "performance": _enum_field("Performance", [("standard","Standard"),("premium","Premium (SSD)")], "standard"),
        "accountType": _enum_field("Storage Account Type", acct_opts, accountType),
        "namespace":   _enum_field("File Structure", ns_opts, namespace),
        "accessTier":  _enum_field("Access Tier", tier_opts, accessTier),
        "redundancy":  _enum_field("Redundancy", redund_opts, redundancy),
        "storageGB":   _num_field("Storage (GB/month)", 1),
        "writeOps":    _ops_field("Write Operations (× 10,000/month)"),
    }
    if has_iter_w:
        props["iterWriteOps"] = _ops_field("Iterative Write Operations (× 10,000/month)")
    if has_iter_r:
        props["iterReadOps"]  = _ops_field("Iterative Read Operations (× 10,000/month)")
    if has_create:
        props["listCreateOps"] = _ops_field("List and Create Container Operations (× 10,000/month)")

    props["readOps"] = _ops_field("Read Operations (× 10,000/month)")

    # High-priority archive rehydration is only surfaced for GPv2 archive accounts.
    if has_priority:
        props["priorityReadOps"] = _num_field(
            "Archive High Priority Read (Operations/month)", 0,
            "Raw operation count — priced at $50.00 per 10,000 operations",
        )

    if has_other:
        props["otherOps"] = _ops_field("Other Operations (× 10,000/month)")

    if has_retrieval:
        props["retrievalGB"] = _gb_field(
            "Data Retrieval (GB/month)",
            "Hot = free · Cool = ~$0.01/GB · Cold = ~$0.03/GB · Archive = ~$0.02/GB",
        )
    if has_priority:
        props["priorityRetrievalGB"] = _gb_field(
            "Archive High Priority Retrieval (GB/month)",
            "Expedited rehydration — ~$0.10/GB",
        )
    if has_geo:
        props["geoReplicationGB"] = _gb_field(
            "Geo-Replication Data Transfer (GB/month)",
            "Bandwidth used to replicate data to secondary region",
        )
    return {"type": "object", "title": "Block Blob Storage", "properties": props}, _defaults(props)


def _calc_block_blob(fd, offers, region):
    performance = fd.get("performance", "standard")

    if performance == "premium":
        ns_type = fd.get("namespace", "flat")
        redund  = fd.get("redundancy", "lrs")
        base    = f"premium-block-blob-{ns_type}-{redund}"
        gb      = float(fd.get("storageGB", 0) or 0)
        write   = float(fd.get("writeOps",  0) or 0)
        read    = float(fd.get("readOps",   0) or 0)
        create  = float(fd.get("createOps", 0) or 0)
        other   = float(fd.get("otherOps",  0) or 0)
        return {
            "capacity":          round(gb     * _flat(offers, base,                              region), 4),
            "write_operations":  round(write  * _flat(offers, f"{base}-write-operations",        region), 4),
            "read_operations":   round(read   * _flat(offers, f"{base}-read-operations",         region), 4),
            "create_operations": round(create * _flat(offers, f"{base}-create-operations",       region), 4),
            "other_operations":  round(other  * _flat(offers, f"{base}-other-operations",        region), 4),
        }

    acct_type = fd.get("accountType", "gpv2")
    namespace = fd.get("namespace",   "flat")
    tier      = fd.get("accessTier",  "hot")
    redund    = fd.get("redundancy",  "lrs")
    base      = _block_blob_base_key(offers, acct_type, namespace, tier, redund)
    is_structured_standard = namespace == "structured" and acct_type in ("gpv2", "blob")
    has_create             = not is_structured_standard
    has_other              = namespace == "flat"
    has_priority           = acct_type == "gpv2" and tier == "archive"
    geo_key                = _block_blob_geo_key(acct_type, tier, redund, performance, namespace)
    write_key              = _block_blob_meter_key(offers, acct_type, namespace, tier, redund, "write-operations")
    read_key               = _block_blob_meter_key(offers, acct_type, namespace, tier, redund, "read-operations")
    other_key              = _block_blob_meter_key(offers, acct_type, namespace, tier, redund, "other-operations")
    retrieval_key          = _block_blob_meter_key(offers, acct_type, namespace, tier, redund, "data-retrieval")
    priority_read_key      = _block_blob_meter_key(offers, acct_type, namespace, tier, redund, "priority-read-operations")
    priority_retrieval_key = _block_blob_meter_key(offers, acct_type, namespace, tier, redund, "priority-data-retrieval")
    iter_write_key         = _block_blob_meter_key(offers, acct_type, namespace, tier, redund, "iterative-write-operations")
    iter_read_key          = _block_blob_meter_key(offers, acct_type, namespace, tier, redund, "iterative-read-operations")
    create_key             = _block_blob_meter_key(offers, acct_type, namespace, tier, redund, "create-operations")

    gb       = float(fd.get("storageGB",        0) or 0)
    write    = float(fd.get("writeOps",         0) or 0)
    read     = float(fd.get("readOps",          0) or 0)
    other    = float(fd.get("otherOps",         0) or 0) if has_other else 0.0
    list_cre = float(fd.get("listCreateOps",    0) or 0) if has_create else 0.0
    iter_w   = float(fd.get("iterWriteOps",     0) or 0) if is_structured_standard else 0.0
    iter_r   = float(fd.get("iterReadOps",      0) or 0) if is_structured_standard else 0.0
    retr_gb  = float(fd.get("retrievalGB",      0) or 0) if tier in ("cool","cold","archive") else 0.0
    geo_gb   = float(fd.get("geoReplicationGB", 0) or 0) if geo_key else 0.0
    # Azure only surfaces high-priority archive rehydration on GPv2 archive accounts.
    priority_read_ops = float(fd.get("priorityReadOps",      0) or 0) if has_priority else 0.0
    priority_retr_gb  = float(fd.get("priorityRetrievalGB",  0) or 0) if has_priority else 0.0

    breakdown = {
        "capacity":         round(_capacity(offers, base, region, gb), 4),
        "write_operations": round(write   * _flat(offers, write_key,                  region), 4),
        "read_operations":  round(read    * _flat(offers, read_key,                   region), 4),
        "other_operations": round(other   * _flat(offers, other_key,                  region), 4),
        "data_retrieval":   round(retr_gb * _flat(offers, retrieval_key,              region), 4),
        "geo_replication":  round(geo_gb  * _flat(offers, geo_key,                    region), 4) if geo_key else 0.0,
        "priority_read_operations": round((priority_read_ops / 10000) * _flat(offers, priority_read_key,      region), 4),
        "priority_data_retrieval":  round(priority_retr_gb  * _flat(offers, priority_retrieval_key, region), 4),
    }
    if is_structured_standard:
        breakdown["iterative_write_operations"] = round(iter_w * _flat(offers, iter_write_key, region), 4)
        breakdown["iterative_read_operations"]  = round(iter_r * _flat(offers, iter_read_key,  region), 4)
    if has_create:
        breakdown["list_create_operations"] = round(list_cre * _flat(offers, create_key, region), 4)
    return breakdown


# ════════════════════════════════════════════════════════════════════════════════
# DATA LAKE GEN2
#
# Key pattern:  general-purpose-v2-data-lake-{namespace}-{tier}-{redund}
# Metadata key: general-purpose-v2-data-lake-metadata-{tier}-{redund}
#               (no namespace segment; covers hot/cold/premium tiers + selected redundancies)
# Geo key:      {base}-data-write (prices are $0.00 — Azure includes geo transfer in capacity)
#
# Multipliers:
#   iterative-write-operations: price unit is 'per100'  → user inputs units of 100  → ×1
#   iterative-read-operations:  price unit is 'per10k'  → user inputs units of 10k  → ×1
#   Both are direct: user input × price = cost (no multiplier needed)
# ════════════════════════════════════════════════════════════════════════════════

def _build_data_lake_schema(offers, regions, region, namespace, accessTier, redundancy, performance="standard"):
    # ── Performance type (Standard / Premium) ──────────────────────────────
    # Premium = fixed tier (no hot/cool/cold/archive), only LRS/ZRS redundancy
    perf_opts = _derive_options(
        offers,
        lambda p: f"general-purpose-v2-data-lake-flat-{'premium' if p == 'premium' else 'hot'}-lrs",
        ["standard", "premium"], _PERF_LABELS,
    )
    performance = _first_valid(perf_opts, performance)

    # When premium is selected, force tier immediately so stale params (e.g. tier=standard,
    # accessTier=hot) from the frontend cannot produce invalid metadata key lookups.
    if performance == "premium":
        accessTier = "premium"

    # ── Namespace ──────────────────────────────────────────────────────────
    if performance == "premium":
        ns_opts = _derive_options(
            offers,
            lambda ns: f"general-purpose-v2-data-lake-{ns}-premium-lrs",
            ["flat", "structured"], _NS_LABELS,
        )
    else:
        ns_opts = _derive_options(
            offers,
            lambda ns: f"general-purpose-v2-data-lake-{ns}-hot-lrs",
            ["flat", "structured"], _NS_LABELS,
        )
    namespace = _first_valid(ns_opts, namespace)

    # ── Access Tier ────────────────────────────────────────────────────────
    if performance == "premium":
        # Premium has no hot/cool/cold/archive tiers — just a single "Premium" tier.
        # Use same (slug, label) tuple format that _derive_options returns.
        tier_opts = [("premium", "Premium")]
        # accessTier already forced to "premium" above
    else:
        tier_opts = _derive_options(
            offers,
            lambda t: f"general-purpose-v2-data-lake-{namespace}-{t}-lrs",
            ["hot", "cool", "cold", "archive"], _TIER_LABELS,
        )
        accessTier = _first_valid(tier_opts, accessTier if accessTier != "premium" else "hot")

    # ── Redundancy ─────────────────────────────────────────────────────────
    redund_opts = _derive_options(
        offers,
        lambda r: f"general-purpose-v2-data-lake-{namespace}-{accessTier}-{r}",
        _REDUND_ORDER, _REDUND_LABELS,
    )
    redundancy = _first_valid(redund_opts, redundancy)
    base = f"general-purpose-v2-data-lake-{namespace}-{accessTier}-{redundancy}"

    # ── Feature flags ──────────────────────────────────────────────────────
    is_premium    = performance == "premium"
    is_archive    = (not is_premium) and accessTier == "archive"
    has_retrieval = (not is_premium) and accessTier in ("cool", "cold", "archive")
    meta_key      = _data_lake_metadata_key(performance, redundancy)
    has_metadata  = namespace == "structured" and (performance == "premium" or accessTier == "hot") and _has_price(offers, meta_key)
    has_data_write = (not is_premium) and _has_price(offers, f"{base}-data-write")
    # Query Acceleration: only hot/cool tiers, flat ns (+ structured for some tiers)
    has_qa        = (not is_premium) and _has_price(
        offers, f"{base}-query-acceleration-data-scanned"
    )
    has_iter_write = _has_price(offers, f"{base}-iterative-write-operations")
    has_iter_read  = _has_price(offers, f"{base}-iterative-read-operations")
    has_other_ops  = _has_price(offers, f"{base}-other-operations")
    # Priority read/retrieval only exist for archive tier
    has_priority  = is_archive and _has_price(offers, f"{base}-priority-read-operations")

    # ── Build props ────────────────────────────────────────────────────────
    props = {
        "region":      _region_field(regions, region),
        "performance": _enum_field("Type", perf_opts, performance),
        "namespace":   _enum_field("File Structure", ns_opts, namespace),
        "accessTier":  _enum_field("Access Tier", tier_opts, accessTier),
        "redundancy":  _enum_field("Redundancy", redund_opts, redundancy),
        "storageGB":   _num_field("Capacity (GB/month)", 1),
        # ── TRANSACTIONS section (mirrors Azure calculator) ──────────────
        "writeOps":    _ops_field("Write Operations (× 10,000/month)"),
        "readOps":     _ops_field("Read Operations (× 10,000/month)"),
    }

    # Priority read (archive high-priority rehydration) — in Transactions section.
    # Azure's UI takes RAW operation count (not units-of-10k like standard ops).
    if has_priority:
        props["priorityReadOps"] = _num_field(
            "Archive High Priority Read (Operations/month)", 0,
            "Raw operation count — priced at $50.00 per 10,000 operations",
        )

    # Query Acceleration (appears in Transactions section on Azure)
    if has_qa:
        props["qaDataReturnedGB"] = _gb_field(
            "Query Acceleration — Data Returned (GB/month)",
            "GB of data returned via query acceleration",
        )
        props["qaDataScannedGB"] = _gb_field(
            "Query Acceleration — Data Scanned (GB/month)",
            "GB of data scanned via query acceleration",
        )

    # ── OTHER OPERATIONS AND METADATA STORAGE METERS section ──────────────
    if has_iter_read:
        props["iterReadOps"]  = _ops_field("Iterative Read Operations (× 10,000/month)")
    if has_iter_write:
        props["iterWriteOps"] = _num_field(
            "Iterative Write Operations (× 100/month)", 0,
            "Units of 100 ops (e.g. 10 = 1,000 ops/month)",
        )
    if has_other_ops:
        props["otherOps"] = _ops_field("All Other Operations (× 10,000/month)")

    if has_retrieval:
        props["retrievalGB"] = _gb_field(
            "Data Retrieval (GB/month)",
            "Cool = ~$0.01/GB · Cold = ~$0.03/GB · Archive = ~$0.02/GB",
        )
    # Archive high-priority retrieval (expedited rehydration)
    if has_priority:
        props["priorityRetrievalGB"] = _gb_field(
            "Archive High Priority Retrieval (GB/month)",
            "Expedited rehydration — ~$0.10/GB",
        )
    if has_metadata:
        props["metadataGB"] = _gb_field(
            "Metadata Storage (GB/month)",
            "ACL/folder structure overhead (Hierarchical Namespace)",
        )
    if has_data_write:
        props["dataWriteGB"] = _gb_field(
            "Data Write / Geo-Replication Transfer (GB/month)",
            "Bandwidth used to replicate data to the secondary region (typically $0.00 for Data Lake)",
        )

    return {"type": "object", "title": "Data Lake Gen2", "properties": props}, _defaults(props)


def _calc_data_lake(fd, offers, region):
    performance = fd.get("performance", "standard")
    namespace   = fd.get("namespace",   "flat")
    tier        = fd.get("accessTier",  "hot")
    redund      = fd.get("redundancy",  "lrs")

    # Premium overrides tier
    if performance == "premium":
        tier = "premium"

    is_archive = tier == "archive"
    base       = f"general-purpose-v2-data-lake-{namespace}-{tier}-{redund}"
    meta_key   = _data_lake_metadata_key(performance, redund)
    show_meta  = namespace == "structured" and (performance == "premium" or tier == "hot") and _has_price(offers, meta_key)
    has_iter_write = _has_price(offers, f"{base}-iterative-write-operations")
    has_iter_read  = _has_price(offers, f"{base}-iterative-read-operations")
    has_data_write = _has_price(offers, f"{base}-data-write")

    gb        = float(fd.get("storageGB",    0) or 0)
    write     = float(fd.get("writeOps",     0) or 0)
    read      = float(fd.get("readOps",      0) or 0)
    other     = float(fd.get("otherOps",     0) or 0)   # 0 if field was hidden for archive
    iter_w    = float(fd.get("iterWriteOps", 0) or 0) if has_iter_write else 0.0
    iter_r    = float(fd.get("iterReadOps",  0) or 0) if has_iter_read else 0.0
    retr_gb   = float(fd.get("retrievalGB",  0) or 0) if tier in ("cool","cold","archive") else 0.0
    geo_gb    = float(fd.get("dataWriteGB",  0) or 0) if has_data_write else 0.0
    qa_ret_gb = float(fd.get("qaDataReturnedGB", 0) or 0)
    qa_scn_gb = float(fd.get("qaDataScannedGB",  0) or 0)
    # Archive high-priority (expedited rehydration) fields
    # priorityReadOps is raw ops (Azure UI) → divide by 10,000 for per-10k price units
    priority_read = float(fd.get("priorityReadOps",     0) or 0) if is_archive else 0.0
    priority_retr = float(fd.get("priorityRetrievalGB", 0) or 0) if is_archive else 0.0
    meta_gb   = float(fd.get("metadataGB", 0) or 0) if show_meta else 0.0

    # Iterative-write: priced 'per100' in metadata, user inputs units of 100 => ×1 (no multiplier)
    # Iterative-read:  priced 'per10k' in metadata, user inputs units of 10k => ×1 (no multiplier)
    breakdown = {
        "capacity":                   round(_capacity(offers, base, region, gb), 4),
        "write_operations":           round(write   * _flat(offers, f"{base}-write-operations",  region), 4),
        "read_operations":            round(read    * _flat(offers, f"{base}-read-operations",   region), 4),
        "iterative_read_operations":  round(iter_r  * _flat(offers, f"{base}-iterative-read-operations",  region), 4),
        "iterative_write_operations": round(iter_w  * _flat(offers, f"{base}-iterative-write-operations", region), 4),
        "other_operations":           round(other   * _flat(offers, f"{base}-other-operations",  region), 4),
        "data_retrieval":             round(retr_gb * _flat(offers, f"{base}-data-retrieval",    region), 4),
        # Archive high-priority (expedited rehydration)
        # priority_read is raw ops count → divide by 10,000 to get per-10k price units
        "priority_read_operations":   round((priority_read / 10000) * _flat(offers, f"{base}-priority-read-operations", region), 4),
        "priority_data_retrieval":    round(priority_retr * _flat(offers, f"{base}-priority-data-retrieval",  region), 4),
        "metadata":                   round(meta_gb * _flat(offers, meta_key, region), 4),
        # Data write / geo-replication bandwidth (usually $0.00 for Data Lake)
        "data_write":                 round(geo_gb  * _flat(offers, f"{base}-data-write",        region), 4),
        # Query Acceleration
        "query_acceleration_data_returned": round(qa_ret_gb * _flat(offers, f"{base}-query-acceleration-data-returned", region), 4),
        "query_acceleration_data_scanned":  round(qa_scn_gb * _flat(offers, f"{base}-query-acceleration-data-scanned",  region), 4),
    }
    return breakdown


# ════════════════════════════════════════════════════════════════════════════════
# PAGE BLOBS (UNMANAGED DISKS)
#
# Azure UI structure (Tier → Account Type):
#
#  Tier = Standard
#    Account Type = General Purpose V2  → general-purpose-v2-page-{redund}
#      Redundancy: LRS, ZRS, GRS, RA-GRS, GZRS, RA-GZRS
#      Ops: write-operations, write-io-operations, read-operations, read-io-operations
#           (all per 10k, distinct rates) — no delete key on v2
#    Account Type = General Purpose V1  → general-purpose-page-{redund}
#      Redundancy: LRS, GRS, RA-GRS (no ZRS/GZRS)
#      Ops: write/read/delete all share the same $0.00036/10k rate
#           IO-ops keys exist in metadata but are empty (no prices) — not shown
#      No "tier" sub-selection for GPv1 — account type IS the full selector
#
#  Tier = Premium  → Premium Unmanaged Disks (separate product, not a v2 sub-tier)
#    general-purpose-page-{size}-premium-unmanaged-disks  (LRS)
#    general-purpose-page-zrs-{size}-premium-unmanaged-disks  (ZRS)
#    Fixed disk sizes: P4(32GiB) … P60(8TiB), billed per month (flat rate)
#    Snapshot: general-purpose-page-premium-unmanaged-disks-snapshot (per GB)
# ════════════════════════════════════════════════════════════════════════════════

_PAGE_DISK_SIZES = ["p4", "p6", "p10", "p15", "p20", "p30", "p40", "p50", "p60"]
_PAGE_DISK_LABELS = {
    "p4":  "P4 (32 GiB)",  "p6":  "P6 (64 GiB)",   "p10": "P10 (128 GiB)",
    "p15": "P15 (256 GiB)","p20": "P20 (512 GiB)",  "p30": "P30 (1 TiB)",
    "p40": "P40 (2 TiB)",  "p50": "P50 (4 TiB)",    "p60": "P60 (8 TiB)",
}
_PAGE_TIER_LABELS  = {"standard": "Standard", "premium": "Premium"}
_PAGE_ACCT_LABELS  = {"gpv2": "General Purpose V2", "gpv1": "General Purpose V1"}
_PAGE_PREM_REDUND_LABELS = {"lrs": "LRS", "zrs": "ZRS"}
_PAGE_GPV1_REDUND_ORDER  = ["lrs", "grs", "ra-grs"]   # ZRS/GZRS never existed for GPv1 page


def _build_page_blob_schema(offers, regions, region, tier="standard", accountType="gpv2",
                            redundancy="lrs", diskSize="p10"):
    tier_opts = [("standard", "Standard"), ("premium", "Premium")]

    # ── Premium Unmanaged Disks ───────────────────────────────────────────────
    if tier == "premium":
        prem_redund_opts = _derive_options(
            offers,
            lambda r: f"general-purpose-page-{'zrs-' if r == 'zrs' else ''}p10-premium-unmanaged-disks",
            ["lrs", "zrs"], _PAGE_PREM_REDUND_LABELS,
        )
        redundancy = _first_valid(prem_redund_opts, redundancy)

        size_opts = _derive_options(
            offers,
            lambda s: f"general-purpose-page-{'zrs-' if redundancy == 'zrs' else ''}{s}-premium-unmanaged-disks",
            _PAGE_DISK_SIZES, _PAGE_DISK_LABELS,
        )
        diskSize = _first_valid(size_opts, diskSize)

        props = {
            "region":      _region_field(regions, region),
            "tier":        _enum_field("Tier", tier_opts, "premium"),
            "redundancy":  _enum_field("Redundancy", prem_redund_opts, redundancy),
            "diskSize":    _enum_field("Disk Size", size_opts, diskSize),
            "diskCount":   _num_field("Number of Disks", 1),
            "snapshotGB":  _gb_field("Snapshot Storage (GB/month)",
                                     "Billed per GB of snapshot data"),
        }
        return {"type": "object", "title": "Page Blobs — Premium Unmanaged Disks", "properties": props}, _defaults(props)

    # ── Standard: GPv1 ────────────────────────────────────────────────────────
    if accountType == "gpv1":
        acct_opts   = [("gpv2", "General Purpose V2"), ("gpv1", "General Purpose V1")]
        redund_opts = _derive_options(
            offers,
            lambda r: f"general-purpose-page-{r}",
            _PAGE_GPV1_REDUND_ORDER, _REDUND_LABELS,
        )
        redundancy = _first_valid(redund_opts, redundancy if redundancy in ("lrs","grs","ra-grs") else "lrs")
        has_geo    = redundancy in ("grs", "ra-grs")

        props = {
            "region":       _region_field(regions, region),
            "tier":         _enum_field("Tier", tier_opts, "standard"),
            "accountType":  _enum_field("Storage Account Type", acct_opts, "gpv1"),
            "redundancy":   _enum_field("Redundancy", redund_opts, redundancy),
            "storageGB":    _num_field("Storage (GB/month)", 1),
            # GPv1: single shared rate for all ops ($0.00036/10k)
            "writeOps":     _ops_field("Write Operations (× 10,000/month)"),
            "readOps":      _ops_field("Read Operations (× 10,000/month)"),
            "deleteOps":    _ops_field("Delete Operations (× 10,000/month)"),
        }
        if has_geo:
            props["geoReplicationGB"] = _gb_field(
                "Geo-Replication Data Transfer (GB/month)",
                "Bandwidth charged for replicating data to secondary region",
            )
        return {"type": "object", "title": "Page Blobs — Standard (GPv1)", "properties": props}, _defaults(props)

    # ── Standard: GPv2 ────────────────────────────────────────────────────────
    acct_opts   = [("gpv2", "General Purpose V2"), ("gpv1", "General Purpose V1")]
    redund_opts = _derive_options(
        offers,
        lambda r: f"general-purpose-v2-page-{r}",
        _REDUND_ORDER, _REDUND_LABELS,
    )
    redundancy = _first_valid(redund_opts, redundancy)
    has_geo    = redundancy in _GEO_REDUND

    props = {
        "region":       _region_field(regions, region),
        "tier":         _enum_field("Tier", tier_opts, "standard"),
        "accountType":  _enum_field("Storage Account Type", acct_opts, "gpv2"),
        "redundancy":   _enum_field("Redundancy", redund_opts, redundancy),
        "storageGB":    _num_field("Storage (GB/month)", 1),
        # Operations for Page Blobs attached as Unmanaged Disks (performed by VM)
        # Single global rate key: general-purpose-v2-unmanaged-disks-transactions ($0.0015/10k)
        "vmOps":        _ops_field("Operations — Unmanaged Disks / VM (× 10,000/month)"),
        # Operations for Page Blobs (Non-disks) — distinct rates per meter
        "writeOps":     _ops_field("Write Operations (× 10,000/month)"),
        "writeIOOps":   _ops_field("Write Additional IO Units (× 10,000/month)"),
        "readOps":      _ops_field("Read Operations (× 10,000/month)"),
        "readIOOps":    _ops_field("Read Additional IO Units (× 10,000/month)"),
        # Delete is $0.00 on v2 keys — not shown
    }
    if has_geo:
        props["geoReplicationGB"] = _gb_field(
            "Geo-Replication Data Transfer (GB/month)",
            "Bandwidth charged for replicating data to secondary region",
        )
    return {"type": "object", "title": "Page Blobs — Standard (GPv2)", "properties": props}, _defaults(props)


def _calc_page_blob(fd, offers, region):
    tier      = fd.get("tier",        "standard")
    acct_type = fd.get("accountType", "gpv2")
    redund    = fd.get("redundancy",  "lrs")

    # ── Premium Unmanaged Disks ───────────────────────────────────────────────
    if tier == "premium":
        disk_size  = fd.get("diskSize",  "p10")
        disk_count = float(fd.get("diskCount",  1) or 1)
        snap_gb    = float(fd.get("snapshotGB", 0) or 0)
        cap_key  = (f"general-purpose-page-zrs-{disk_size}-premium-unmanaged-disks"
                    if redund == "zrs" else
                    f"general-purpose-page-{disk_size}-premium-unmanaged-disks")
        snap_key = "general-purpose-page-premium-unmanaged-disks-snapshot"
        return {
            "disk_cost":        round(disk_count * _flat(offers, cap_key,  region), 4),
            "snapshot_storage": round(snap_gb    * _flat(offers, snap_key, region), 4),
        }

    # ── Standard GPv1 — all ops share one flat rate ───────────────────────────
    if acct_type == "gpv1":
        base    = f"general-purpose-page-{redund}"
        gb      = float(fd.get("storageGB",        0) or 0)
        write   = float(fd.get("writeOps",         0) or 0)
        read    = float(fd.get("readOps",          0) or 0)
        delete  = float(fd.get("deleteOps",        0) or 0)
        geo_gb  = float(fd.get("geoReplicationGB", 0) or 0) if redund in ("grs","ra-grs") else 0.0
        ops_rate = _flat(offers, f"{base}-write-operations", region)  # same rate for all ops
        return {
            "capacity":          round(gb     * _flat(offers, base,                       region), 4),
            "write_operations":  round(write  * ops_rate,                                        4),
            "read_operations":   round(read   * ops_rate,                                        4),
            "delete_operations": round(delete * ops_rate,                                        4),
            "geo_replication":   round(geo_gb * _flat(offers, f"{base}-data-transfer",   region), 4),
        }

    # ── Standard GPv2 — distinct rates per op type ───────────────────────────
    base     = f"general-purpose-v2-page-{redund}"
    gb       = float(fd.get("storageGB",        0) or 0)
    vm_ops   = float(fd.get("vmOps",            0) or 0)
    write    = float(fd.get("writeOps",         0) or 0)
    write_io = float(fd.get("writeIOOps",       0) or 0)
    read     = float(fd.get("readOps",          0) or 0)
    read_io  = float(fd.get("readIOOps",        0) or 0)
    geo_gb   = float(fd.get("geoReplicationGB", 0) or 0) if redund in _GEO_REDUND else 0.0
    return {
        "capacity":              round(gb       * _flat(offers, base,                                          region), 4),
        "vm_disk_transactions":  round(vm_ops   * _flat(offers, "general-purpose-v2-unmanaged-disks-transactions", region), 4),
        "write_operations":      round(write    * _flat(offers, f"{base}-write-operations",                    region), 4),
        "write_io_operations":   round(write_io * _flat(offers, f"{base}-write-io-operations",                 region), 4),
        "read_operations":       round(read     * _flat(offers, f"{base}-read-operations",                     region), 4),
        "read_io_operations":    round(read_io  * _flat(offers, f"{base}-read-io-operations",                  region), 4),
        "geo_replication":       round(geo_gb   * _flat(offers, f"{base}-data-transfer",                       region), 4),
    }


# ════════════════════════════════════════════════════════════════════════════════
# DISPATCH + ROUTE HANDLERS
# ════════════════════════════════════════════════════════════════════════════════

def _dispatch_schema(offers, regions, storage_type, region, args):
    a = args  # shorthand
    builders = {
        "block-blob":    lambda: _build_block_blob_schema(
                             offers, regions, region,
                             a.get("accountType","gpv2"), a.get("namespace","flat"),
                             a.get("accessTier","hot"),   a.get("redundancy","lrs"),
                             a.get("performance","standard")),
        "data-lake":     lambda: _build_data_lake_schema(
                             offers, regions, region,
                             a.get("namespace","flat"), a.get("accessTier","hot"),
                             a.get("redundancy","lrs"), a.get("performance","standard")),
        "page-blob":     lambda: _build_page_blob_schema(
                             offers, regions, region,
                             a.get("tier","standard"), a.get("accountType","gpv2"),
                             a.get("redundancy","lrs"), a.get("diskSize","p10")),
        "queue":         lambda: _build_queue_schema(
                             offers, regions, region,
                             a.get("accountType","gpv2"), a.get("redundancy","lrs")),
        "table":         lambda: _build_table_schema(
                             offers, regions, region,
                             a.get("tier","standard"), a.get("redundancy","lrs")),
    }
    fn = builders.get(storage_type)
    return fn() if fn else None

_CALCULATORS = {
    "block-blob":    _calc_block_blob,
    "data-lake":     _calc_data_lake,
    "page-blob":     _calc_page_blob,
    "queue":         _calc_queue,
    "table":         _calc_table,
}


@ns.route("/schema")
class StorageSchema(Resource):
    def get(self):
        storage_type = request.args.get("storageType", "block-blob")
        region       = request.args.get("region", "us-east")

        # Schema endpoint now fetches offers (cached) so options are metadata-driven
        data, err = _fetch()
        if err:
            return {"error": f"Azure pricing fetch error: {err}"}, 502

        offers  = data.get("offers", {})
        regions = _derive_regions(offers)

        try:
            result = _dispatch_schema(offers, regions, storage_type, region, dict(request.args))
        except Exception as e:
            return {"error": f"Schema build error: {e}"}, 400
        if result is None:
            return {"error": f"Unknown storageType: '{storage_type}'"}, 400
        schema, defaults = result
        return {"schema": schema, "defaults": defaults}


_calc_model = ns.model("StorageCalc", {
    "storage_type": fields.String(required=True),
    "region":       fields.String(required=True),
    "form_data":    fields.Raw(required=True),
})

@ns.route("/calculate")
class StorageCalculate(Resource):
    @ns.expect(_calc_model)
    def post(self):
        body         = ns.payload or {}
        storage_type = body.get("storage_type", "block-blob")
        region       = body.get("region", "us-east")
        fd           = body.get("form_data", {})
        if not region or fd is None:
            return {"error": "region and form_data are required"}, 400
        calc_fn = _CALCULATORS.get(storage_type)
        if not calc_fn:
            return {"error": f"Unknown storageType: '{storage_type}'"}, 400
        data, err = _fetch()
        if err:
            return {"error": f"Azure pricing fetch error: {err}"}, 502
        offers    = data.get("offers", {})
        breakdown = calc_fn(fd, offers, region)
        total     = round(sum(breakdown.values()), 4)
        return {
            "monthly_total": total,
            "breakdown":     breakdown,
            "currency":      "USD",
            "region":        region,
            "storage_type":  storage_type,
        }
