import { useState, useEffect, useCallback, useMemo } from "react";
import validator from "@rjsf/validator-ajv8";
import AzureVMInstanceField from "./fields/AzureVMInstanceField";
import { S } from "./styles";
import { VMFormDataContext } from "./VMFormDataContext";
import Form from "@rjsf/core";

const API = "http://localhost:5000/api";

const CUSTOM_FIELDS = { azureVMInstanceField: AzureVMInstanceField };

const UI_VM = {
  region:           { "ui:widget": "select" },
  operatingSystem:  { "ui:widget": "select" },
  linuxType:        { "ui:widget": "select" },
  tier:             { "ui:widget": "select" },
  instanceSelector: { "ui:field": "azureVMInstanceField" },
  addHybridBenefit: { "ui:widget": "hidden" },
};

const UI_DISK = {
  diskTier:   { "ui:widget": "select" },
  redundancy: { "ui:widget": "select" },
  diskSize:   { "ui:widget": "select" },
};

// ── Managed Disks Section ─────────────────────────────────────────────────────
function ManagedDisksSection({ region, onResult, onDiskTierChange, onRedundancyChange, onDiskSizeChange, onDiskCountChange }) {
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
    const tierChanged  = fd.diskTier   !== prev.diskTier;
    const redChanged   = fd.redundancy !== prev.redundancy;
    const sizeChanged  = fd.diskSize   !== prev.diskSize;
    const countChanged = fd.count      !== prev.count;
    if (tierChanged)  onDiskTierChange(fd.diskTier);
    if (redChanged)   onRedundancyChange(fd.redundancy);
    if (sizeChanged)  onDiskSizeChange(fd.diskSize);
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
          region, disk_tier: diskTier, redundancy,
          disk_size: diskSize, disk_count: diskCount, form_data: formData,
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
    dataTransferType:  { "ui:widget": "select", "ui:enumNames": ["Inter Region", "Internet Egress"] },
    sourceRegion:      { "ui:widget": "select" },
    destinationRegion: { "ui:widget": "select" },
    routedVia:         { "ui:widget": "select", "ui:enumNames": ["Microsoft Global Network", "Public Internet"] },
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

// ── VM Calculator ─────────────────────────────────────────────────────────────
export default function VMCalculator() {
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

  const [diskTier, setDiskTier]      = useState("standardssd");
  const [diskRedundancy, setDiskRed] = useState("lrs");
  const [diskSize, setDiskSize]      = useState("e10");
  const [diskCount, setDiskCount]    = useState(1);
  const [diskResult, setDiskResult]  = useState(null);
  const [txnResult, setTxnResult]    = useState(null);
  const [bwResult, setBwResult]      = useState(null);

  const normalizeVMFormData = useCallback((fd = {}) => {
    const { category, series, instanceSize, instanceSelector, ...rest } = fd || {};
    const normalizedSelector = { ...(instanceSelector || {}) };
    if (!normalizedSelector.category && category)         normalizedSelector.category     = category;
    if (!normalizedSelector.series && series)             normalizedSelector.series        = series;
    if (!normalizedSelector.instanceSize && instanceSize) normalizedSelector.instanceSize  = instanceSize;
    return { ...rest, instanceSelector: normalizedSelector };
  }, []);

  const fetchSchema = useCallback(async (r, o, lt, t, prev = {}) => {
    setLoading(true); setError(null); setVmResult(null);
    try {
      const p = new URLSearchParams({ region: r, operatingSystem: o, linuxType: lt, tier: t });
      const res  = await fetch(`${API}/vm/schema?${p}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);

      const normalizedPrev = normalizeVMFormData(prev);
      const resetInstanceSelector = r !== region || o !== os || lt !== linuxType || t !== tier;
      const next = {
        ...json.defaults,
        region: r, operatingSystem: o, linuxType: lt, tier: t,
        count: normalizedPrev.count !== undefined ? normalizedPrev.count : json.defaults.count,
        hours: normalizedPrev.hours !== undefined ? normalizedPrev.hours : json.defaults.hours,
        addHybridBenefit: ahb,
        instanceSelector: resetInstanceSelector ? {} : (normalizedPrev.instanceSelector || {}),
      };
      setSchema(json.schema);
      setFormData(next);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [ahb, linuxType, normalizeVMFormData, region, os, tier]);

  useEffect(() => { fetchSchema(region, os, linuxType, tier); }, []);

  const handleChange = ({ formData: fd }) => {
    const merged     = { ...formData, ...fd };
    const normalized = normalizeVMFormData(merged);
    const next = {
      ...normalized,
      region: normalized?.region || merged?.region || formData?.region || region,
      count:  normalized?.count  ?? formData.count  ?? schema?.properties?.count?.default ?? 1,
      hours:  normalized?.hours  ?? formData.hours  ?? schema?.properties?.hours?.default ?? 730,
      addHybridBenefit: normalized?.addHybridBenefit ?? formData.addHybridBenefit ?? ahb,
    };
    setFormData(next); setVmResult(null);
    const nr  = next.region          || region;
    const no  = next.operatingSystem || os;
    const nlt = next.linuxType       || linuxType;
    const nt  = next.tier            || tier;
    if (nr !== region || no !== os || nlt !== linuxType || nt !== tier) {
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
                formContext={{ region: formData?.region || region, parentFormData: formData }}
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
