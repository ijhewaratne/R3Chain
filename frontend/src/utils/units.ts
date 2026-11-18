import type { EnergyUnitDisplay } from "../types/api";

export const JOULES_PER_MWH = 3.6e9;
export const JOULES_PER_GJ = 1.0e9;

export function formatHeatCost(
  currencyPerJoule: number | null | undefined,
  unitDisplay: EnergyUnitDisplay,
  currency = "EUR"
): string {
  if (currencyPerJoule === null || currencyPerJoule === undefined || !Number.isFinite(currencyPerJoule)) {
    return "n/a";
  }
  const factor = unitDisplay === "GJ" ? JOULES_PER_GJ : JOULES_PER_MWH;
  return `${(currencyPerJoule * factor).toFixed(2)} ${currency}/${unitDisplay}`;
}

export function formatEnergy(joules: number | null | undefined, unitDisplay: EnergyUnitDisplay): string {
  if (joules === null || joules === undefined || !Number.isFinite(joules)) {
    return "n/a";
  }
  const factor = unitDisplay === "GJ" ? JOULES_PER_GJ : JOULES_PER_MWH;
  return `${(joules / factor).toFixed(1)} ${unitDisplay}`;
}

export function formatCurrency(value: number | null | undefined, currency = "EUR"): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "n/a";
  }
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value)} ${currency}`;
}

export function formatAssumptionValue(value: unknown, unit: string | null): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    const formatted = Math.abs(value) > 0 && (Math.abs(value) < 0.001 || Math.abs(value) >= 1e6)
      ? value.toExponential(3)
      : Number.isInteger(value)
        ? String(value)
        : value.toFixed(4).replace(/\.?0+$/, "");
    return unit ? `${formatted} ${unit}` : formatted;
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return unit ? `${String(value)} ${unit}` : String(value);
}
