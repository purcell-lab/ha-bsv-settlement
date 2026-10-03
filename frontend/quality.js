// Keep the explicit warning allowlist aligned with session_review.WARNING_FLAGS.
export const meteringWarnings = {
  interval_energy_allocation_estimated: "Energy allocation across price intervals is estimated.",
  not_a_final_bill: "This sensor-derived account is provisional, not a certified bill.",
  "import:energy_without_matching_state": "Charging energy was recorded while the charger state did not indicate charging.",
  "export:energy_without_matching_state": "Export energy was recorded while the charger state did not indicate discharging.",
};
export function qualityFlags(flags = []) {
  return {
    warnings: flags.filter(f => Object.hasOwn(meteringWarnings, f)),
    blockers: flags.filter(f => !Object.hasOwn(meteringWarnings, f)),
  };
}
export function warningMessage(flags = []) {
  return qualityFlags(flags).warnings.map(f => meteringWarnings[f]).join(" ");
}
