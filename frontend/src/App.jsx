import { useState } from "react";
import FunctionsCalculator from "./FunctionsCalculator";
import VMCalculator from "./VMCalculator";
import StorageCalculator from "./StorageCalculator";
import { S } from "./styles";

const SERVICES = [
  { slug: "functions", label: "⚡ Azure Functions" },
  { slug: "vm",        label: "🖥 Virtual Machines" },
  { slug: "storage",   label: "🗄 Storage Accounts" },
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
      {service === "storage"   && <StorageCalculator />}
    </div>
  );
}