import { useState, useEffect, useCallback, useMemo, createContext } from "react";
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import AzureVMInstanceField from "./fields/AzureVMInstanceField";

const API = "http://localhost:5000/api";

// ── Create context for passing full form data to custom fields ────────────────
export const VMFormDataContext = createContext(null);

const S = {
  app: {
    fontFamily: "'Segoe UI', system-ui, sans-serif",
    maxWidth: 880, margin: "0 auto", padding: "24px 20px",
    background: "#f6f8fc", minHeight: "100vh", color: "#1a1a2e",
  },
  header: { marginBottom: 24 },
  title: { fontSize: 22, fontWeight: 600, color: "#0078d4", margin: 0 },
  sub: { fontSize: 13, color: "#888", marginTop: 4 },
  serviceRow: { display: "flex", gap: 8, marginBottom: 20 },
  svcBtn: (active) => ({
    padding: "9px 22px", borderRadius: 20, cursor: "pointer", fontSize: 14,
    fontWeight: active ? 600 : 400, transition: "all 0.15s",
    border: active ? "1.5px solid #0078d4" : "1.5px solid #d0d7e0",
    background: active ? "#e6f2fd" : "#fff", color: active ? "#0078d4" : "#555",
  }),
  card: {
    background: "#fff", border: "1px solid #e0e7ef", borderRadius: 8,
    padding: "20px 24px", marginBottom: 16, boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
  },
  secTitle: {
    fontSize: 11, fontWeight: 600, color: "#0078d4", textTransform: "uppercase",
    letterSpacing: "0.06em", marginBottom: 14, marginTop: 0,
  },
  tierBtn: (active) => ({
    padding: "7px 16px", borderRadius: 20, cursor: "pointer", fontSize: 13,
    fontWeight: active ? 600 : 400,
    border: active ? "1.5px solid #0078d4" : "1.5px solid #d0d7e0",
    background: active ? "#e6f2fd" : "#fff", color: active ? "#0078d4" : "#555",
  }),
  calcBtn: (loading) => ({
    background: loading ? "#aac8e8" : "#0078d4", color: "#fff",
    border: "none", borderRadius: 6, padding: "10px 24px",
    fontSize: 14, fontWeight: 600, cursor: loading ? "not-allowed" : "pointer", marginTop: 8,
  }),
  result: {
    background: "linear-gradient(135deg, #0078d4, #005fa3)",
    borderRadius: 8, padding: "20px 24px", color: "#fff", marginTop: 16,
  },
  resultLabel: { fontSize: 13, opacity: 0.85, marginBottom: 6 },
  resultAmount: { fontSize: 36, fontWeight: 700, margin: 0, letterSpacing: "-0.5px" },
  resultSub: { fontSize: 12, opacity: 0.75, marginTop: 4 },
  error: {
    background: "#fef0f0", border: "1px solid #fca5a5", borderRadius: 6,
    padding: "10px 14px", color: "#b91c1c", fontSize: 13, marginTop: 10,
  },
  loading: { padding: "32px 0", textAlign: "center", color: "#888", fontSize: 14 },
  radioRow: { display: "flex", gap: 12, marginBottom: 16, alignItems: "center", flexWrap: "wrap" },
  radioLabel: (active) => ({
    display: "flex", alignItems: "center", gap: 6, cursor: "pointer",
    padding: "6px 14px", borderRadius: 16, fontSize: 13,
    border: active ? "1.5px solid #0078d4" : "1.5px solid #d0d7e0",
    background: active ? "#e6f2fd" : "#fff", color: active ? "#0078d4" : "#555",
    fontWeight: active ? 600 : 400,
  }),
  addOnHeader: {
    fontSize: 13, fontWeight: 600, color: "#555", textTransform: "uppercase",
    letterSpacing: "0.05em", margin: "24px 0 12px",
    borderBottom: "1px solid #e0e7ef", paddingBottom: 8,
  },
  totalCard: {
    background: "linear-gradient(135deg, #0078d4, #005fa3)",
    borderRadius: 8, padding: "20px 24px", color: "#fff", marginTop: 8,
  },
  totalRow: { display: "flex", justifyContent: "space-between", fontSize: 14, opacity: 0.9, marginBottom: 6 },
  totalAmount: { fontSize: 36, fontWeight: 700, margin: "8px 0 0", letterSpacing: "-0.5px" },
  premiumNote: { fontSize: 12, color: "#666", fontStyle: "italic", padding: "8px 0" },
};

// ── rjsf field registry — maps "ui:field" keys to custom components ───────────
const CUSTOM_FIELDS = {
  azureVMInstanceField: AzureVMInstanceField,
};

// ── uiSchemas ─────────────────────────────────────────────────────────────────

const UI_VM = {
  region:            { "ui:widget": "select" },
  operatingSystem:   { "ui:widget": "select" },
  linuxType:         { "ui:widget": "select" },
  tier:              { "ui:widget": "select" },
  // Delegate the entire instanceSelector object to our custom field.
  instanceSelector:  { "ui:field": "azureVMInstanceField" },
  addHybridBenefit:  { "ui:widget": "hidden" },
};

const UI_DISK = {
  diskTier:   { "ui:widget": "select" },
  redundancy: { "ui:widget": "select" },
  diskSize:   { "ui:widget": "select" },
};
const UI_BW = {
  dataTransferType:  { "ui:widget": "select" },
  sourceRegion:      { "ui:widget": "select" },
  destinationRegion: { "ui:widget": "select" },
};

// ── Functions Calculator ──────────────────────────────────────────────────────
const FUNCTION_TIERS = [
  { slug: "consumption",     label: "Consumption" },
  { slug: "flexconsumption", label: "Flex Consumption" },
  { slug: "premium",         label: "Premium" },
];

function FunctionsCalculator() {
  const [tier, setTier]         = useState("consumption");
  const [region, setRegion]     = useState("us-west");
  const [schema, setSchema]     = useState(null);
  const [formData, setFormData] = useState({});
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState(null);
  const [calculating, setCalc]  = useState(false);
  const [result, setResult]     = useState(null);

  const fetchSchema = useCallback(async (t, r) => {
    setLoading(true); setError(null); setResult(null);
    try {
      const res  = await fetch(`${API}/functions/schema?tier=${t}&region=${r}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setSchema(json.schema);
      setFormData({ ...json.defaults, region: r });
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchSchema(tier, region); }, []);

  const handleChange = ({ formData: fd }) => {
    setFormData(fd); setResult(null);
    if (fd?.region && fd.region !== region) {
      setRegion(fd.region);
      fetchSchema(tier, fd.region);
    }
  };

  const handleCalculate = async () => {
    setCalc(true); setError(null);
    console.log({tier, region: formData.region || region, form_data: formData})
    try {
      const res  = await fetch(`${API}/functions/calculate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier, region: formData.region || region, form_data: formData }),
      });
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setResult(json);
    } catch (e) { setError(e.message); }
    finally { setCalc(false); }
  };

  const regionLabel = schema?.properties?.region?.enumNames?.[
    schema?.properties?.region?.enum?.indexOf(formData.region)
  ] || formData.region;

  return (
    <>
      <div style={S.card}>
        <p style={S.secTitle}>Hosting Plan</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {FUNCTION_TIERS.map(t => (
            <button key={t.slug} style={S.tierBtn(tier === t.slug)}
              onClick={() => { setTier(t.slug); fetchSchema(t.slug, region); }} disabled={loading}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <div style={S.card}>
        <p style={S.secTitle}>Configuration</p>
        {loading && <div style={S.loading}>Loading from Azure…</div>}
        {error   && <div style={S.error}>⚠ {error}</div>}
        {!loading && !error && schema && (
          <Form
            schema={schema}
            uiSchema={{ region: { "ui:widget": "select" } }}
            formData={formData}
            onChange={handleChange}
            validator={validator}
            onSubmit={handleCalculate}
          >
            <button type="submit" style={S.calcBtn(calculating)} disabled={calculating}>
              {calculating ? "Calculating…" : "Calculate Monthly Cost →"}
            </button>
          </Form>
        )}
      </div>
      {result && (
        <div style={S.result}>
          <p style={S.resultLabel}>Estimated monthly cost</p>
          <p style={S.resultAmount}>
            ${result.monthly_total.toFixed(2)}
            <span style={{ fontSize: 18, fontWeight: 400, opacity: 0.8 }}> / month</span>
          </p>
          <p style={S.resultSub}>
            {FUNCTION_TIERS.find(t => t.slug === tier)?.label} · {regionLabel} · USD
          </p>
        </div>
      )}
    </>
  );
}

// ── VM Calculator ─────────────────────────────────────────────────────────────
function VMCalculator() {
  const [region, setRegion]       = useState("us-east");
  const [os, setOs]               = useState("linux");
  const [linuxType, setLinuxType] = useState("ubuntu");
  const [tier, setTier]           = useState("standard");
  const [ahb, setAhb]             = useState(false);
  const [schema, setSchema]       = useState(null);
  const [formData, setFormData]   = useState({});
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState(null);
  const [calculating, setCalc]    = useState(false);
  const [vmResult, setVmResult]   = useState(null);

  // Disk state — shared with storage transactions
  const [diskTier, setDiskTier]      = useState("standardssd");
  const [diskRedundancy, setDiskRed] = useState("lrs");
  const [diskSize, setDiskSize]      = useState("e10");
  const [diskCount, setDiskCount]    = useState(1);
  const [diskResult, setDiskResult]  = useState(null);
  const [txnResult, setTxnResult]    = useState(null);
  const [bwResult, setBwResult]      = useState(null);

  const normalizeVMFormData = useCallback((fd = {}) => {
    const {
      category,
      series,
      instanceSize,
      instanceSelector,
      ...rest
    } = fd || {};

    const normalizedSelector = {
      ...(instanceSelector || {}),
    };

    if (!normalizedSelector.category && category) {
      normalizedSelector.category = category;
    }
    if (!normalizedSelector.series && series) {
      normalizedSelector.series = series;
    }
    if (!normalizedSelector.instanceSize && instanceSize) {
      normalizedSelector.instanceSize = instanceSize;
    }

    return {
      ...rest,
      instanceSelector: normalizedSelector,
    };
  }, []);

  const fetchSchema = useCallback(async (r, o, lt, t, prev = {}) => {
    setLoading(true); setError(null); setVmResult(null);
    try {
      const p = new URLSearchParams({ region: r, operatingSystem: o, linuxType: lt, tier: t });
      const res  = await fetch(`${API}/vm/schema?${p}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);

      const normalizedPrev = normalizeVMFormData(prev);
      const next = {
        ...json.defaults,
        region: r, operatingSystem: o, linuxType: lt, tier: t,
        count: normalizedPrev.count !== undefined ? normalizedPrev.count : json.defaults.count,
        hours: normalizedPrev.hours !== undefined ? normalizedPrev.hours : json.defaults.hours,
        addHybridBenefit: ahb,
        // Preserve instanceSelector selection across schema refreshes.
        instanceSelector: normalizedPrev.instanceSelector || {},
      };
      setSchema(json.schema);
      setFormData(next);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [ahb, normalizeVMFormData]);

  useEffect(() => { fetchSchema(region, os, linuxType, tier); }, []);

  const handleChange = ({ formData: fd }) => {
    // Merge incoming data with existing formData to preserve fields not in fd
    const merged = { ...formData, ...fd };
    console.log("🔧 handleChange - incoming fd:", fd);
    console.log("🔧 handleChange - merged:", merged);
    console.log("🔧 handleChange - formData region:", formData?.region);
    
    const normalized = normalizeVMFormData(merged);
    console.log("🔧 handleChange - normalized:", normalized);
    
    const next = {
      ...normalized,
      region: normalized?.region || merged?.region || formData?.region || region,
      count: normalized?.count ?? formData.count ?? schema?.properties?.count?.default ?? 1,
      hours: normalized?.hours ?? formData.hours ?? schema?.properties?.hours?.default ?? 730,
      addHybridBenefit: normalized?.addHybridBenefit ?? formData.addHybridBenefit ?? ahb,
    };
    console.log("🔧 handleChange - next (final):", next);
    console.log("🔧 handleChange - next.region:", next.region);
    
    setFormData(next); setVmResult(null);
    const nr  = next.region          || region;
    const no  = next.operatingSystem || os;
    const nlt = next.linuxType       || linuxType;
    const nt  = next.tier            || tier;
    console.log("🔧 handleChange - checking schema refetch. nr:", nr, "region:", region, "changed?", nr !== region);
    if (nr !== region || no !== os || nlt !== linuxType || nt !== tier) {
      console.log("🔧 handleChange - refetching schema");
      setRegion(nr); setOs(no); setLinuxType(nlt); setTier(nt);
      fetchSchema(nr, no, nlt, nt, next);
    }
  };

  const handleAhb = (val) => {
    setAhb(val);
    setFormData(fd => ({ ...fd, addHybridBenefit: val }));
    setVmResult(null);
  };

  const handleCalculate = async ({ formData: submittedFormData } = {}) => {
    setCalc(true); setError(null);
    const payloadFormData = normalizeVMFormData(submittedFormData || formData);
    console.log({ tier, region: payloadFormData.region || region, form_data: payloadFormData });
    try {
      const res = await fetch(`${API}/vm/calculate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          region: payloadFormData.region || region,
          form_data: { ...payloadFormData, addHybridBenefit: ahb },
        }),
      });
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setVmResult(json);
    } catch (e) { setError(e.message); }
    finally { setCalc(false); }
  };

  const curRegion   = formData.region || region;
  const isWindows   = os === "windows";
  const regionLabel = schema?.properties?.region?.enumNames?.[
    schema?.properties?.region?.enum?.indexOf(curRegion)
  ] || curRegion;

  // Memoized — only rebuilds when schema or isWindows changes,
  // NOT on every count/hours keystroke
  const rjsfSchema = useMemo(() => {
    if (!schema) return null;
    return {
      ...schema,
      properties: Object.fromEntries(
        Object.entries(schema.properties).filter(([k]) =>
          !(k === "linuxType" && isWindows)
        )
      ),
    };
  }, [schema, isWindows]);

  const anyResult    = vmResult || diskResult || txnResult || bwResult;
  const runningTotal = (vmResult?.monthly_total  || 0)
                     + (diskResult?.monthly_total || 0)
                     + (txnResult?.monthly_total  || 0)
                     + (bwResult?.monthly_total   || 0);

  return (
    <>
      <div style={S.card}>
        <p style={S.secTitle}>Virtual Machine Configuration</p>
        {loading && <div style={S.loading}>Loading from Azure…</div>}
        {error   && <div style={S.error}>⚠ {error}</div>}
        {!loading && !error && rjsfSchema && (
          <>
            {isWindows && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 12, color: "#888", marginBottom: 6 }}>License</div>
                <div style={S.radioRow}>
                  <label style={S.radioLabel(!ahb)}>
                    <input type="radio" name="license" checked={!ahb}
                      onChange={() => handleAhb(false)} style={{ accentColor: "#0078d4" }} />
                    License Included
                  </label>
                  <label style={S.radioLabel(ahb)}>
                    <input type="radio" name="license" checked={ahb}
                      onChange={() => handleAhb(true)} style={{ accentColor: "#0078d4" }} />
                    Azure Hybrid Benefit
                  </label>
                </div>
              </div>
            )}
            <VMFormDataContext.Provider value={formData}>
              <Form
                schema={rjsfSchema}
                uiSchema={UI_VM}
                fields={CUSTOM_FIELDS}
                formData={formData}
                formContext={{ 
                  region: formData?.region || region,
                  parentFormData: formData 
                }}
                onChange={handleChange}
                validator={validator}
                onSubmit={handleCalculate}
              >
                <button type="submit" style={S.calcBtn(calculating)} disabled={calculating}>
                  {calculating ? "Calculating…" : "Calculate VM Cost →"}
                </button>
              </Form>
            </VMFormDataContext.Provider>
          </>
        )}
        {vmResult && (
          <div style={S.result}>
            <p style={S.resultLabel}>VM compute — estimated monthly cost</p>
            <p style={S.resultAmount}>
              ${vmResult.monthly_total.toFixed(2)}
              <span style={{ fontSize: 18, fontWeight: 400, opacity: 0.8 }}> / month</span>
            </p>
            <p style={S.resultSub}>
              {isWindows
                ? (ahb ? "Windows · Azure Hybrid Benefit" : "Windows · License Included")
                : `Linux · ${schema?.properties?.linuxType?.enumNames?.[schema?.properties?.linuxType?.enum?.indexOf(formData.linuxType)] || formData.linuxType}`}
              {" · "}{regionLabel} · PAYG · USD
            </p>
          </div>
        )}
      </div>

      <p style={S.addOnHeader}>Add-on Services</p>

      <ManagedDisksSection
        region={curRegion}
        onResult={setDiskResult}
        onDiskTierChange={setDiskTier}
        onRedundancyChange={setDiskRed}
        onDiskSizeChange={setDiskSize}
        onDiskCountChange={setDiskCount}
      />

      <StorageTxnSection
        region={curRegion}
        diskTier={diskTier}
        redundancy={diskRedundancy}
        diskSize={diskSize}
        diskCount={diskCount}
        onResult={setTxnResult}
      />

      <BandwidthSection region={curRegion} onResult={setBwResult} />

      {anyResult && (
        <div style={S.totalCard}>
          <p style={{ ...S.resultLabel, marginBottom: 4 }}>Monthly Cost Breakdown</p>
          {vmResult   && <div style={S.totalRow}><span>Virtual Machine</span><span>${vmResult.monthly_total.toFixed(2)}</span></div>}
          {diskResult && <div style={S.totalRow}><span>Managed Disks</span><span>${diskResult.monthly_total.toFixed(2)}</span></div>}
          {txnResult  && <div style={S.totalRow}><span>Storage Transactions</span><span>${txnResult.monthly_total.toFixed(2)}</span></div>}
          {bwResult   && <div style={S.totalRow}><span>Bandwidth</span><span>${bwResult.monthly_total.toFixed(2)}</span></div>}
          <div style={{ borderTop: "1px solid rgba(255,255,255,0.3)", marginTop: 8, paddingTop: 8 }}>
            <p style={S.totalAmount}>
              ${runningTotal.toFixed(2)}
              <span style={{ fontSize: 18, fontWeight: 400, opacity: 0.8 }}> / month total</span>
            </p>
          </div>
        </div>
      )}
    </>
  );
}

// ── Managed Disks Section ─────────────────────────────────────────────────────
function ManagedDisksSection({
  region,
  onResult,
  onDiskTierChange,
  onRedundancyChange,
  onDiskSizeChange,
  onDiskCountChange,
}) {
  const [schema, setSchema]     = useState(null);
  const [formData, setFormData] = useState({});
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState(null);
  const [calculating, setCalc]  = useState(false);

  const fetchSchema = useCallback(async (r, diskTier, redundancy, diskSize) => {
    setLoading(true); setError(null);
    try {
      const p = new URLSearchParams({ region: r });
      if (diskTier)   p.set("diskTier",   diskTier);
      if (redundancy) p.set("redundancy", redundancy);
      if (diskSize)   p.set("diskSize",   diskSize);
      const res  = await fetch(`${API}/vm/managed-disks/schema?${p}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setSchema(json.schema);
      setFormData(json.defaults);
      onDiskTierChange(json.defaults.diskTier);
      onRedundancyChange(json.defaults.redundancy);
      onDiskSizeChange(json.defaults.diskSize);
      onDiskCountChange(json.defaults.count);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [onDiskCountChange, onDiskSizeChange, onDiskTierChange, onRedundancyChange]);

  useEffect(() => { fetchSchema(region); }, [region]);

  const handleChange = ({ formData: fd }) => {
    const prev = formData;
    setFormData(fd); onResult(null);
    const tierChanged = fd.diskTier   !== prev.diskTier;
    const redChanged  = fd.redundancy !== prev.redundancy;
    const sizeChanged = fd.diskSize   !== prev.diskSize;
    const countChanged = fd.count     !== prev.count;
    if (tierChanged) onDiskTierChange(fd.diskTier);
    if (redChanged)  onRedundancyChange(fd.redundancy);
    if (sizeChanged) onDiskSizeChange(fd.diskSize);
    if (countChanged) onDiskCountChange(fd.count);
    if (tierChanged || redChanged) fetchSchema(region, fd.diskTier, fd.redundancy, fd.diskSize);
  };

  const handleCalculate = async () => {
    setCalc(true);
    try {
      const res = await fetch(`${API}/vm/managed-disks/calculate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ region, form_data: formData }),
      });
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      onResult(json);
    } catch (e) { setError(e.message); }
    finally { setCalc(false); }
  };

  return (
    <div style={S.card}>
      <p style={S.secTitle}>Managed Disks</p>
      {loading && <div style={S.loading}>Loading from Azure…</div>}
      {error   && <div style={S.error}>⚠ {error}</div>}
      {!loading && !error && schema && (
        <Form schema={schema} uiSchema={UI_DISK} formData={formData}
          onChange={handleChange} validator={validator} onSubmit={handleCalculate}>
          <button type="submit" style={S.calcBtn(calculating)} disabled={calculating}>
            {calculating ? "Calculating…" : "Calculate Disk Cost →"}
          </button>
        </Form>
      )}
    </div>
  );
}

// ── Storage Transactions Section ──────────────────────────────────────────────
function StorageTxnSection({ region, diskTier, redundancy, diskSize, diskCount, onResult }) {
  const [schema, setSchema]     = useState(null);
  const [formData, setFormData] = useState({});
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState(null);
  const [calculating, setCalc]  = useState(false);
  const isPremium = diskTier === "premiumssd";

  const fetchSchema = useCallback(async (dt) => {
    setLoading(true); setError(null);
    try {
      const res  = await fetch(`${API}/vm/storage-transactions/schema?diskTier=${dt}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setSchema(json.schema);
      setFormData(json.defaults);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchSchema(diskTier); }, [diskTier]);

  const handleCalculate = async () => {
    if (isPremium) { onResult({ monthly_total: 0 }); return; }
    setCalc(true);
    try {
      const res = await fetch(`${API}/vm/storage-transactions/calculate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          region,
          disk_tier: diskTier,
          redundancy,
          disk_size: diskSize,
          disk_count: diskCount,
          form_data: formData,
        }),
      });
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      onResult(json);
    } catch (e) { setError(e.message); }
    finally { setCalc(false); }
  };

  return (
    <div style={S.card}>
      <p style={S.secTitle}>Storage Transactions</p>
      {isPremium && <p style={S.premiumNote}>Premium SSD disks do not incur storage transaction charges.</p>}
      {loading && !isPremium && <div style={S.loading}>Loading…</div>}
      {error   && <div style={S.error}>⚠ {error}</div>}
      {!loading && !error && schema && !isPremium && (
        <Form schema={schema} uiSchema={{}} formData={formData}
          onChange={({ formData: fd }) => { setFormData(fd); onResult(null); }}
          validator={validator} onSubmit={handleCalculate}>
          <button type="submit" style={S.calcBtn(calculating)} disabled={calculating}>
            {calculating ? "Calculating…" : "Calculate Transaction Cost →"}
          </button>
        </Form>
      )}
      {isPremium && (
        <button style={S.calcBtn(false)} onClick={() => onResult({ monthly_total: 0 })}>
          Confirm $0.00 →
        </button>
      )}
    </div>
  );
}

// ── Bandwidth Section ─────────────────────────────────────────────────────────
function BandwidthSection({ region, onResult }) {
  const [schema, setSchema]     = useState(null);
  const [formData, setFormData] = useState({});
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState(null);
  const [calculating, setCalc]  = useState(false);

  const fetchSchema = useCallback(async (r) => {
    setLoading(true); setError(null);
    try {
      const res  = await fetch(`${API}/vm/bandwidth/schema?region=${r}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setSchema(json.schema);
      setFormData({ ...json.defaults, sourceRegion: r });
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchSchema(region); }, [region]);

  const handleCalculate = async () => {
    setCalc(true);
    try {
      const srcRegion = formData.sourceRegion || region;
      const res = await fetch(`${API}/vm/bandwidth/calculate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ region: srcRegion, form_data: formData }),
      });
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      onResult(json);
    } catch (e) { setError(e.message); }
    finally { setCalc(false); }
  };

  const isInternetEgress = formData.dataTransferType === "internetegress";

  const rjsfSchema = schema ? {
    ...schema,
    properties: Object.fromEntries(
      Object.entries(schema.properties).filter(([k]) => {
        if (isInternetEgress && k === "destinationRegion") return false;
        if (!isInternetEgress && k === "routedVia")        return false;
        return true;
      })
    ),
  } : null;

  const uiSchemaBw = {
    dataTransferType:  {
      "ui:widget": "select",
      "ui:enumNames": ["Inter Region", "Internet Egress"],
    },
    sourceRegion:      { "ui:widget": "select" },
    destinationRegion: { "ui:widget": "select" },
    routedVia:         {
      "ui:widget": "select",
      "ui:enumNames": ["Microsoft Global Network", "Public Internet"],
    },
  };

  return (
    <div style={S.card}>
      <p style={S.secTitle}>Bandwidth</p>
      {loading && <div style={S.loading}>Loading from Azure…</div>}
      {error   && <div style={S.error}>⚠ {error}</div>}
      {!loading && !error && rjsfSchema && (
        <Form schema={rjsfSchema} uiSchema={uiSchemaBw} formData={formData}
          onChange={({ formData: fd }) => { setFormData(fd); onResult(null); }}
          validator={validator} onSubmit={handleCalculate}>
          <button type="submit" style={S.calcBtn(calculating)} disabled={calculating}>
            {calculating ? "Calculating…" : "Calculate Bandwidth Cost →"}
          </button>
        </Form>
      )}
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────
const SERVICES = [
  { slug: "functions", label: "⚡ Azure Functions" },
  { slug: "vm",        label: "🖥 Virtual Machines" },
];

export default function App() {
  const [service, setService] = useState("functions");
  return (
    <div style={S.app}>
      <div style={S.header}>
        <h1 style={S.title}>Azure Pricing Calculator</h1>
        <p style={S.sub}>Live prices from Azure · All figures in USD · Monthly estimates</p>
      </div>
      <div style={S.serviceRow}>
        {SERVICES.map(s => (
          <button key={s.slug} style={S.svcBtn(service === s.slug)} onClick={() => setService(s.slug)}>
            {s.label}
          </button>
        ))}
      </div>
      {service === "functions" && <FunctionsCalculator />}
      {service === "vm"        && <VMCalculator />}
    </div>
  );
}
