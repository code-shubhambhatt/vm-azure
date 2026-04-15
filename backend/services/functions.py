"""
functions.py — Azure Functions pricing service
Endpoints: /api/functions/*
"""

from flask import request
from flask_restx import Namespace, Resource, fields
from .shared import fetch_pricing, fetch_metadata, get_graduated_price

ns = Namespace("functions", description="Azure Functions pricing")

AZURE_URL = (
    "https://azure.microsoft.com/api/v2/pricing/functions/calculator/"
    "?culture=en-us&discount=mca"
)

ALL_REGIONS = [
    {"slug": "asia-pacific-east",       "label": "East Asia"},
    {"slug": "asia-pacific-southeast",  "label": "Southeast Asia"},
    {"slug": "australia-central",       "label": "Australia Central"},
    {"slug": "australia-central-2",     "label": "Australia Central 2"},
    {"slug": "australia-east",          "label": "Australia East"},
    {"slug": "australia-southeast",     "label": "Australia Southeast"},
    {"slug": "austria-east",            "label": "Austria East"},
    {"slug": "belgium-central",         "label": "Belgium Central"},
    {"slug": "brazil-south",            "label": "Brazil South"},
    {"slug": "brazil-southeast",        "label": "Brazil Southeast"},
    {"slug": "canada-central",          "label": "Canada Central"},
    {"slug": "canada-east",             "label": "Canada East"},
    {"slug": "central-india",           "label": "Central India"},
    {"slug": "south-india",             "label": "South India"},
    {"slug": "west-india",              "label": "West India"},
    {"slug": "chile-central",           "label": "Chile Central"},
    {"slug": "denmark-east",            "label": "Denmark East"},
    {"slug": "europe-north",            "label": "North Europe"},
    {"slug": "europe-west",             "label": "West Europe"},
    {"slug": "france-central",          "label": "France Central"},
    {"slug": "france-south",            "label": "France South"},
    {"slug": "germany-north",           "label": "Germany North"},
    {"slug": "germany-west-central",    "label": "Germany West Central"},
    {"slug": "indonesia-central",       "label": "Indonesia Central"},
    {"slug": "israel-central",          "label": "Israel Central"},
    {"slug": "italy-north",             "label": "Italy North"},
    {"slug": "japan-east",              "label": "Japan East"},
    {"slug": "japan-west",              "label": "Japan West"},
    {"slug": "korea-central",           "label": "Korea Central"},
    {"slug": "korea-south",             "label": "Korea South"},
    {"slug": "malaysia-west",           "label": "Malaysia West"},
    {"slug": "mexico-central",          "label": "Mexico Central"},
    {"slug": "new-zealand-north",       "label": "New Zealand North"},
    {"slug": "norway-east",             "label": "Norway East"},
    {"slug": "norway-west",             "label": "Norway West"},
    {"slug": "poland-central",          "label": "Poland Central"},
    {"slug": "qatar-central",           "label": "Qatar Central"},
    {"slug": "south-africa-north",      "label": "South Africa North"},
    {"slug": "south-africa-west",       "label": "South Africa West"},
    {"slug": "spain-central",           "label": "Spain Central"},
    {"slug": "sweden-central",          "label": "Sweden Central"},
    {"slug": "sweden-south",            "label": "Sweden South"},
    {"slug": "switzerland-north",       "label": "Switzerland North"},
    {"slug": "switzerland-west",        "label": "Switzerland West"},
    {"slug": "uae-central",             "label": "UAE Central"},
    {"slug": "uae-north",               "label": "UAE North"},
    {"slug": "united-kingdom-south",    "label": "UK South"},
    {"slug": "united-kingdom-west",     "label": "UK West"},
    {"slug": "us-central",              "label": "Central US"},
    {"slug": "us-east",                 "label": "East US"},
    {"slug": "us-east-2",               "label": "East US 2"},
    {"slug": "us-north-central",        "label": "North Central US"},
    {"slug": "us-south-central",        "label": "South Central US"},
    {"slug": "us-west-central",         "label": "West Central US"},
    {"slug": "us-west",                 "label": "West US"},
    {"slug": "us-west-2",               "label": "West US 2"},
    {"slug": "us-west-3",               "label": "West US 3"},
    {"slug": "usgov-arizona",           "label": "US Gov Arizona"},
    {"slug": "usgov-texas",             "label": "US Gov Texas"},
    {"slug": "usgov-virginia",          "label": "US Gov Virginia"},
]

TIERS = [
    {"slug": "consumption",     "label": "Consumption"},
    {"slug": "flexconsumption", "label": "Flex Consumption"},
    {"slug": "premium",         "label": "Premium"},
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_data():
    return fetch_pricing(AZURE_URL, "functions_data")


def _build_region_field():
    return {
        "type": "string",
        "title": "Region",
        "enum": [r["slug"] for r in ALL_REGIONS],
        "enumNames": [r["label"] for r in ALL_REGIONS],
        "default": "us-west",
    }


def _build_consumption_schema(data, region):
    memory_sizes = data.get("memorySizes", [])
    return {
        "type": "object",
        "title": "Azure Functions — Consumption",
        "required": ["region", "memorySize", "executionCount", "executionTime"],
        "properties": {
            "region": _build_region_field(),
            "memorySize": {
                "type": "integer",
                "title": "Memory Size",
                "enum": [int(m["slug"]) for m in memory_sizes],
                "enumNames": [f"{m['slug']} MB" for m in memory_sizes],
                "default": 128,
                "description": "Memory allocated per function execution",
            },
            "executionCount": {
                "type": "number", "title": "Monthly Executions",
                "default": 1000000, "minimum": 0,
                "description": "Total executions per month",
            },
            "executionTime": {
                "type": "number", "title": "Execution Time (ms)",
                "default": 100, "minimum": 1,
                "description": "Average execution duration in milliseconds",
            },
        },
    }


def _build_flex_schema(data, region):
    inst_sizes = data.get("instanceMemorySizes", [])
    return {
        "type": "object",
        "title": "Azure Functions — Flex Consumption",
        "required": ["region", "instanceMemorySize"],
        "properties": {
            "region": _build_region_field(),
            "instanceMemorySize": {
                "type": "integer",
                "title": "Instance Memory Size",
                "enum": [int(m["slug"]) for m in inst_sizes],
                "enumNames": [f"{m['slug']} MB" for m in inst_sizes],
                "default": 2048,
                "description": "Memory size of each function instance",
            },
            "onDemandExecutions": {
                "type": "number", "title": "On Demand — Total Executions / Month (×10)",
                "default": 0, "minimum": 0,
            },
            "onDemandExecutionTime": {
                "type": "number",
                "title": "On Demand — Active Execution Seconds / Instance / Month",
                "default": 0, "minimum": 0,
                "description": "Total active execution seconds per instance per month",
            },
            "onDemandInstances": {
                "type": "number", "title": "On Demand — Active Instances",
                "default": 0, "minimum": 0,
            },
            "alwaysReadyEnabled": {
                "type": "boolean", "title": "Enable Always Ready", "default": False,
            },
            "alwaysReadyInstances": {
                "type": "number", "title": "Always Ready — Baseline Instances",
                "default": 0, "minimum": 0,
            },
            "alwaysReadyBaselineExecTime": {
                "type": "number", "title": "Always Ready — Baseline Exec Seconds / Instance / Month",
                "default": 0, "minimum": 0,
            },
            "alwaysReadyTotalExecutions": {
                "type": "number", "title": "Always Ready — Total Executions / Month (×10)",
                "default": 0, "minimum": 0,
            },
            "alwaysReadyActiveExecTime": {
                "type": "number", "title": "Always Ready — Active Exec Seconds / Instance / Month",
                "default": 0, "minimum": 0,
            },
            "alwaysReadyActiveInstances": {
                "type": "number", "title": "Always Ready — Active Instances",
                "default": 0, "minimum": 0,
            },
        },
    }


def _build_premium_schema(data, region):
    offers = data.get("offers", {})
    instances = []
    for key, offer in sorted(offers.items()):
        if key.endswith("-payg") and key.startswith("ep"):
            slug = key.replace("-payg", "")
            price = offer.get("prices", {}).get(region, {}).get("value", "N/A")
            price_str = f"${price}/hr" if price != "N/A" else ""
            instances.append({
                "slug": slug,
                "label": f"{slug.upper()}: {offer.get('cores')} vCPU, {offer.get('ram')} GB RAM — {price_str}",
            })
    if not instances:
        instances = [
            {"slug": "ep1", "label": "EP1: 1 vCPU, 3.5 GB RAM"},
            {"slug": "ep2", "label": "EP2: 2 vCPU, 7.0 GB RAM"},
            {"slug": "ep3", "label": "EP3: 4 vCPU, 14.0 GB RAM"},
        ]
    return {
        "type": "object",
        "title": "Azure Functions — Premium",
        "required": ["region", "instance", "billingOption", "preWarmedCount", "preWarmedHours"],
        "properties": {
            "region": _build_region_field(),
            "instance": {
                "type": "string", "title": "Instance",
                "enum": [i["slug"] for i in instances],
                "enumNames": [i["label"] for i in instances],
                "default": "ep1",
            },
            "billingOption": {
                "type": "string", "title": "Billing Option",
                "enum": ["payg", "sv-one-year", "sv-three-year"],
                "enumNames": ["Pay as you go", "1 Year Savings Plan", "3 Year Savings Plan"],
                "default": "payg",
            },
            "preWarmedCount": {
                "type": "integer", "title": "Minimum Instances (Pre-warmed)",
                "default": 1, "minimum": 1,
            },
            "preWarmedHours": {
                "type": "number", "title": "Pre-warmed Hours / Month",
                "default": 730, "minimum": 0, "maximum": 744,
            },
            "additionalUnitsCount": {
                "type": "integer", "title": "Additional Scaled-Out Units",
                "default": 0, "minimum": 0,
            },
            "additionalUnitsHours": {
                "type": "number", "title": "Additional Units Hours / Month",
                "default": 0, "minimum": 0, "maximum": 744,
            },
        },
    }


def _get_schema(tier, region, data):
    builders = {
        "consumption":     _build_consumption_schema,
        "flexconsumption": _build_flex_schema,
        "premium":         _build_premium_schema,
    }
    b = builders.get(tier)
    if not b:
        return None, f"Unknown tier: {tier}"
    return b(data, region), None


# ── Pricing engine ────────────────────────────────────────────────────────────

def _calc_consumption(fd, data, region):
    memory_mb   = float(fd.get("memorySize", 128))
    exec_count  = float(fd.get("executionCount", 0))
    exec_time   = float(fd.get("executionTime", 100))
    gb_seconds  = (memory_mb / 1024) * (exec_time / 1000) * exec_count
    go = data.get("graduatedOffers", {})

    # compute-payg: unit is raw GB-seconds (limit=400,000 GB-s free)
    compute_cost = get_graduated_price(
        go.get("compute-payg", {}).get(region, {}).get("prices", []), gb_seconds)

    # requests-payg: unit is MILLIONS of executions (limit=1.0 = 1M free)
    # Must divide exec_count by 1,000,000 before passing to the walker
    request_cost = get_graduated_price(
        go.get("requests-payg", {}).get(region, {}).get("prices", []),
        exec_count / 1_000_000)

    return round(compute_cost + request_cost, 4)


def _calc_flex(fd, data, region):
    mem_gb = float(fd.get("instanceMemorySize", 2048)) / 1024
    go     = data.get("graduatedOffers", {})
    total  = 0.0

    od_execs   = float(fd.get("onDemandExecutions", 0)) * 10
    od_secs    = float(fd.get("onDemandExecutionTime", 0))
    od_inst    = float(fd.get("onDemandInstances", 0))
    od_gb_s    = od_inst * od_secs * mem_gb

    total += get_graduated_price(
        go.get("flex-consumption-on-demand-execution-payg", {}).get(region, {}).get("prices", []),
        od_gb_s)
    total += get_graduated_price(
        go.get("flex-consumption-on-demand-total-execution-payg", {}).get(region, {}).get("prices", []),
        od_execs)

    if fd.get("alwaysReadyEnabled"):
        ar_inst      = float(fd.get("alwaysReadyInstances", 0))
        ar_total_ex  = float(fd.get("alwaysReadyTotalExecutions", 0)) * 10
        ar_base_secs = float(fd.get("alwaysReadyBaselineExecTime", 0))
        ar_act_secs  = float(fd.get("alwaysReadyActiveExecTime", 0))

        total += get_graduated_price(
            go.get("flex-consumption-always-ready-baseline-payg", {}).get(region, {}).get("prices", []),
            ar_inst * 730 * 3600 * mem_gb)
        total += get_graduated_price(
            go.get("flex-consumption-always-ready-execution-payg", {}).get(region, {}).get("prices", []),
            ar_act_secs * mem_gb * ar_total_ex)
        total += get_graduated_price(
            go.get("flex-consumption-always-ready-total-execution-payg", {}).get(region, {}).get("prices", []),
            ar_total_ex)

    return round(total, 4)


def _calc_premium(fd, data, region):
    instance   = fd.get("instance", "ep1")
    billing    = fd.get("billingOption", "payg")
    pw_count   = float(fd.get("preWarmedCount", 1))
    pw_hours   = float(fd.get("preWarmedHours", 730))
    add_count  = float(fd.get("additionalUnitsCount", 0))
    add_hours  = float(fd.get("additionalUnitsHours", 0))

    offers = data.get("offers", {})
    go     = data.get("graduatedOffers", {})

    # Get offer for specs (cores, ram) — billing suffix may vary
    offer  = offers.get(f"{instance}-{billing}") or offers.get(f"{instance}-payg", {})
    cores  = offer.get("cores", 1)
    ram    = offer.get("ram", 3.5)

    # Map billing slug → graduated offer key suffix
    # sv-one-year and sv-three-year use savings plan duration rates
    suffix_map = {
        "payg":         "payg",
        "sv-one-year":  "sv-one-year",
        "sv-three-year":"sv-three-year",
    }
    suffix = suffix_map.get(billing, "payg")

    vcpu_key = f"premium-vcpu-duration-{suffix}"
    mem_key  = f"premium-memory-duration-{suffix}"

    def _rate(key):
        node = go.get(key, {}).get(region)
        if node:
            return node.get("prices", [{}])[0].get("price", {}).get("value", 0)
        return 0

    vcpu_rate = _rate(vcpu_key)
    mem_rate  = _rate(mem_key)

    # Rate per instance per hour = (vCPU rate × cores) + (memory rate × ram GB)
    # Both pre-warmed AND additional scaled-out units are billed at this rate.
    # The flat offer price shown in the dropdown (e.g. $0.0123/hr) is NOT the
    # billing rate — it equals the memory duration rate and is used for UI display only.
    rate_per_instance = vcpu_rate * cores + mem_rate * ram

    total = (
        rate_per_instance * pw_count  * pw_hours +
        rate_per_instance * add_count * add_hours
    )
    return round(total, 4)


# ── Routes ────────────────────────────────────────────────────────────────────

@ns.route("/tiers")
class Tiers(Resource):
    def get(self):
        return {"tiers": TIERS}


@ns.route("/schema")
class Schema(Resource):
    def get(self):
        tier   = request.args.get("tier", "consumption")
        region = request.args.get("region", "us-west")
        data, err = _fetch_data()
        if err:
            return {"error": f"Azure API error: {err}"}, 502
        schema, err = _get_schema(tier, region, data)
        if err:
            return {"error": err}, 400
        az = data.get("schema", {})
        defaults = {
            "region":                    az.get("region", "us-west"),
            "memorySize":                az.get("memorySize", 128),
            "instanceMemorySize":        az.get("instanceMemorySize", 2048),
            "executionCount":            az.get("executionCount", 0),
            "executionTime":             az.get("executionTime", 100),
            "instance":                  az.get("instance", "ep1"),
            "billingOption":             "payg",
            "preWarmedCount":            az.get("preWarmedCount", 1),
            "preWarmedHours":            az.get("preWarmedCountHours", 730),
            "additionalUnitsCount":      az.get("additionalUnitsCount", 0),
            "additionalUnitsHours":      az.get("additionalUnitsCountHours", 0),
            "onDemandExecutions":        0,
            "onDemandExecutionTime":     0,
            "onDemandInstances":         0,
            "alwaysReadyEnabled":        False,
            "alwaysReadyInstances":      0,
            "alwaysReadyBaselineExecTime": 0,
            "alwaysReadyTotalExecutions": 0,
            "alwaysReadyActiveExecTime": 0,
            "alwaysReadyActiveInstances": 0,
        }
        return {"schema": schema, "defaults": defaults}


calculate_model = ns.model("FunctionsCalculate", {
    "tier":      fields.String(required=True),
    "region":    fields.String(required=True),
    "form_data": fields.Raw(required=True),
})


@ns.route("/calculate")
class Calculate(Resource):
    @ns.expect(calculate_model)
    def post(self):
        body      = ns.payload or {}
        tier      = body.get("tier", "consumption")
        region    = body.get("region", "us-west")
        form_data = body.get("form_data", {})
        if not all([tier, region, form_data]):
            return {"error": "tier, region, and form_data are required"}, 400
        data, err = _fetch_data()
        if err:
            return {"error": f"Azure API error: {err}"}, 502
        calcs = {
            "consumption":     _calc_consumption,
            "flexconsumption": _calc_flex,
            "premium":         _calc_premium,
        }
        calc = calcs.get(tier)
        if not calc:
            return {"error": f"Unknown tier: {tier}"}, 400
        total = calc(form_data, data, region)
        return {"monthly_total": total, "currency": "USD", "region": region, "tier": tier}