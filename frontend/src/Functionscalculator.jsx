import { useState, useEffect, useCallback } from "react";
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import { S } from "./styles";

const API = "http://localhost:5000/api";

const FUNCTION_TIERS = [
  { slug: "consumption",     label: "Consumption" },
  { slug: "flexconsumption", label: "Flex Consumption" },
  { slug: "premium",         label: "Premium" },
];

export default function FunctionsCalculator() {
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