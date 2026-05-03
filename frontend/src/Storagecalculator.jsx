import { useState, useEffect, useCallback } from "react";
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import { S } from "./styles";

const API = "http://localhost:5000/api";

const STORAGE_TYPES = [
  { slug: "block-blob",  label: "Block Blob" },
  { slug: "data-lake",   label: "Data Lake Gen2" },
  { slug: "page-blob",   label: "Page Blobs" },
  { slug: "queue",       label: "Queue" },
  { slug: "table",       label: "Table" },
];

// Human-readable labels for every possible breakdown key
const STORAGE_BREAKDOWN_LABELS = {
  capacity:                    "Capacity (Data Stored)",
  transactions:                "Storage Transactions",
  metadata:                    "Metadata Storage",
  data_retrieval:              "Data Retrieval",
  priority_data_retrieval:     "Archive High Priority Retrieval",
  data_write:                  "Data Write / Geo-Replication Transfer",
  geo_replication:             "Geo-Replication Data Transfer",
  vm_disk_transactions:        "Operations — Unmanaged Disks / VM",
  write_operations:            "Write Operations",
  write_io_operations:         "Write Additional IO Units",
  read_operations:             "Read Operations",
  read_io_operations:          "Read Additional IO Units",
  priority_read_operations:    "Archive High Priority Read",
  delete_operations:           "Delete Operations",
  other_operations:            "Other Operations",
  list_create_operations:      "List and Create Container Operations",
  list_operations:             "List Operations",
  iterative_write_operations:  "Iterative Write Operations",
  iterative_read_operations:   "Iterative Read Operations",
  create_operations:           "Create / Delete Operations",
  batch_write_operations:      "Batch Write Operations",
  scan_operations:             "Scan Operations",
  other_meta_operations:       "Other Operations and Metadata Meters",
  disk_cost:                   "Disk Cost (per month)",
  snapshot_storage:            "Snapshot Storage",
  query_acceleration_data_returned: "Query Acceleration — Data Returned",
  query_acceleration_data_scanned:  "Query Acceleration — Data Scanned",
};

const STORAGE_SELECTOR_KEYS = new Set([
  "region", "accessTier", "redundancy", "tier", "diskSize", "namespace",
  "performance", "accountType", "fileStructure",
]);

// All numeric input fields across all 7 types
const STORAGE_NUMERIC_FIELDS = [
  "storageGB", "transactions", "vmOps", "writeOps", "writeIOOps", "readOps", "readIOOps",
  "listOps", "otherOps", "createOps", "listCreateOps",
  "retrievalGB", "geoReplicationGB", "dataWriteGB",
  "metadataGB",
  "iterWriteOps", "iterReadOps",
  "priorityReadOps", "priorityRetrievalGB",
  "batchOps", "scanOps", "deleteOps", "otherMetaOps",
  "provisionedGiB", "provisionedIOPS", "provisionedMBps",
  "snapshotGiB", "softDeleteGiB",
];

const STORAGE_NOTES = {
  "block-blob":  "ℹ Standard: choose account type, namespace, access tier, and redundancy. Premium (SSD): LRS/ZRS only, no access tier. Operations input in units of 10,000.",
  "data-lake":   "ℹ Standard: choose file structure, access tier, and redundancy. Premium: LRS/ZRS only, no access tier tiers. Iterative Write ops input in units of 100 (priced per 100 operations). Query Acceleration available for Hot/Cool tiers.",
  "page-blob":   "ℹ Standard: GPv2 has distinct write/read ops + IO unit rates, full redundancy set. GPv1 has a single flat ops rate, LRS/GRS/RA-GRS only. Premium: fixed monthly price per disk size (P4–P60), LRS or ZRS only.",
  "queue":       "ℹ Choose account type: GPv1 (no geo transfer) or GPv2 (with geo-transfer). Operations in units of 10,000.",
  "table":       "ℹ Standard: ops priced individually (write/read/list/batch/scan/delete/otherMeta). Account Encrypted: same ops but scan maps to read-ops rate; higher capacity price on geo-redundant tiers.",
};

const UI_STORAGE = {
  region:        { "ui:widget": "select" },
  performance:   { "ui:widget": "select" },
  accountType:   { "ui:widget": "select" },
  fileStructure: { "ui:widget": "select" },
  accessTier:    { "ui:widget": "select" },
  tier:          { "ui:widget": "select" },
  diskSize:      { "ui:widget": "select" },
  redundancy:    { "ui:widget": "select" },
  namespace:     { "ui:widget": "select" },
};

export default function StorageCalculator() {
  const [storageType, setStorageType] = useState("block-blob");
  const [schema,      setSchema]      = useState(null);
  const [formData,    setFormData]    = useState({});
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState(null);
  const [calculating, setCalc]        = useState(false);
  const [result,      setResult]      = useState(null);

  // ── Schema fetch ──────────────────────────────────────────────────────────
  const fetchSchema = useCallback(async (type, selectorFd, prevNumFd = {}) => {
    setLoading(true); setError(null); setResult(null);
    try {
      const isPremiumDL = type === "data-lake" && selectorFd.performance === "premium";
      const p = new URLSearchParams({
        storageType:   type,
        region:        selectorFd.region        || "us-east",
        accessTier:    isPremiumDL ? "premium" : (selectorFd.accessTier || "hot"),
        redundancy:    selectorFd.redundancy    || "lrs",
        tier:          selectorFd.tier          || (type === "page-blob" ? "standard" : "standard"),
        diskSize:      selectorFd.diskSize      || "p10",
        namespace:     selectorFd.namespace     || "flat",
        performance:   selectorFd.performance   || "standard",
        accountType:   selectorFd.accountType   || "gpv2",
        fileStructure: selectorFd.fileStructure || "flat",
      });
      const res  = await fetch(`${API}/storage/schema?${p}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);

      // Preserve existing values only when still valid in the new schema.
      const props = json.schema?.properties || {};
      const preserved = {};
      Object.entries(props).forEach(([key, prop]) => {
        const candidate = selectorFd[key] ?? prevNumFd[key];
        if (candidate === undefined || candidate === null || candidate === "") return;
        if (Array.isArray(prop?.enum) && !prop.enum.includes(candidate)) return;
        if (Array.isArray(prop?.oneOf)) {
          const allowed = prop.oneOf.map(o => o?.const);
          if (!allowed.includes(candidate)) return;
        }
        preserved[key] = candidate;
      });

      setSchema(json.schema);
      setFormData({ ...json.defaults, ...preserved });
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, []);

  // Initial load
  useEffect(() => { fetchSchema("block-blob", {}); }, []);

  // ── Storage type selector ─────────────────────────────────────────────────
  const handleTypeChange = (type) => {
    if (type === storageType) return;
    setStorageType(type);
    setResult(null);
    fetchSchema(type, formData, formData);
  };

  // ── RJSF onChange ─────────────────────────────────────────────────────────
  const handleChange = ({ formData: fd }) => {
    setFormData(fd); setResult(null);
    const schemaSelectorKeys = schema?.properties
      ? Object.entries(schema.properties)
          .filter(([, p]) => Array.isArray(p?.enum) || Array.isArray(p?.oneOf))
          .map(([k]) => k)
      : [...STORAGE_SELECTOR_KEYS];
    const selectorChanged = schemaSelectorKeys.some(k => fd[k] !== undefined && fd[k] !== formData[k]);
    if (selectorChanged) fetchSchema(storageType, fd, formData);
  };

  // ── Calculate ─────────────────────────────────────────────────────────────
  const handleCalculate = async ({ formData: submitted } = {}) => {
    setCalc(true); setError(null);
    const fd = submitted || formData;
    try {
      const res = await fetch(`${API}/storage/calculate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          storage_type: storageType,
          region:       fd.region || "us-east",
          form_data:    fd,
        }),
      });
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setResult(json);
    } catch (e) { setError(e.message); }
    finally { setCalc(false); }
  };

  // ── Result subtitle helper ────────────────────────────────────────────────
  const getResultSubtitle = () => {
    if (!schema) return "";
    const label = (field, val) => {
      const p = schema.properties?.[field];
      if (!p) return val || "";
      if (Array.isArray(p.oneOf)) {
        const match = p.oneOf.find(o => o?.const === val);
        if (match?.title) return match.title;
      }
      const idx = p.enum?.indexOf(val);
      return p.enumNames?.[idx] || val || "";
    };
    const parts = [];
    if (storageType === "block-blob") {
      const isPremium = formData.performance === "premium";
      parts.push(isPremium ? "Premium (SSD)" : label("accountType", formData.accountType));
      if (!isPremium) parts.push(label("accessTier", formData.accessTier));
    } else if (storageType === "data-lake") {
      const isPremium = formData.performance === "premium";
      parts.push(isPremium ? "Premium" : "Standard");
      parts.push(label("namespace", formData.namespace));
      if (!isPremium) parts.push(label("accessTier", formData.accessTier));
    } else if (storageType === "page-blob") {
      const isPremium = formData.tier === "premium";
      if (isPremium) {
        parts.push("Premium Unmanaged Disks");
        parts.push(label("diskSize", formData.diskSize));
      } else {
        parts.push("Standard Page Blobs");
        parts.push(label("accountType", formData.accountType));
      }
    } else if (storageType === "queue") {
      parts.push(label("accountType", formData.accountType));
      parts.push("Queue Storage");
    } else if (storageType === "table") {
      parts.push(label("tier", formData.tier));
      parts.push("Table Storage");
    }
    parts.push(label("redundancy", formData.redundancy));
    parts.push(label("region", formData.region));
    parts.push("PAYG · USD");
    return parts.filter(Boolean).join(" · ");
  };

  const infoNote = STORAGE_NOTES[storageType];
  const dynamicUiSchema = { ...UI_STORAGE };
  if (schema?.properties) {
    Object.entries(schema.properties).forEach(([k, p]) => {
      const enumCount = Array.isArray(p?.enum)
        ? p.enum.length
        : Array.isArray(p?.oneOf)
          ? p.oneOf.length
          : null;
      if (enumCount === 1) dynamicUiSchema[k] = { "ui:widget": "hidden" };
    });
  }

  return (
    <>
      {/* ── Storage type selector ── */}
      <div style={S.card}>
        <p style={S.secTitle}>Storage Type</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {STORAGE_TYPES.map(t => (
            <button
              key={t.slug}
              style={S.tierBtn(storageType === t.slug)}
              onClick={() => handleTypeChange(t.slug)}
              disabled={loading}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Configuration form ── */}
      <div style={S.card}>
        <p style={S.secTitle}>Configuration</p>

        <div style={S.infoNote}>
          ℹ Prices are fetched live from Azure on first Calculate and cached for 1 hour.
        </div>

        {loading && <div style={S.loading}>Loading from Azure…</div>}
        {error   && <div style={S.error}>⚠ {error}</div>}

        {!loading && !error && schema && (
          <Form
            schema={schema}
            uiSchema={dynamicUiSchema}
            formData={formData}
            onChange={handleChange}
            validator={validator}
            onSubmit={handleCalculate}
          >
            {infoNote && (
              <div style={{ ...S.infoNote, marginBottom: 12 }}>{infoNote}</div>
            )}
            <button type="submit" style={S.calcBtn(calculating)} disabled={calculating}>
              {calculating ? "Fetching prices…" : "Calculate Monthly Cost →"}
            </button>
          </Form>
        )}

        {/* ── Result ── */}
        {result && (
          <div style={{ ...S.result, marginTop: 16 }}>
            <p style={S.resultLabel}>Estimated monthly cost</p>
            <p style={S.resultAmount}>
              ${result.monthly_total.toFixed(2)}
              <span style={{ fontSize: 18, fontWeight: 400, opacity: 0.8 }}> / month</span>
            </p>
            <p style={S.resultSub}>{getResultSubtitle()}</p>

            {result.breakdown && (
              <table style={S.breakdownTable}>
                <tbody>
                  {Object.entries(result.breakdown)
                    .filter(([, v]) => v > 0)
                    .map(([key, val]) => (
                      <tr key={key} style={S.breakdownRow}>
                        <td style={S.breakdownCell}>
                          {STORAGE_BREAKDOWN_LABELS[key] || key}
                        </td>   
                        <td style={S.breakdownCellRight}>
                          ${val.toFixed(4)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </>
  );
}