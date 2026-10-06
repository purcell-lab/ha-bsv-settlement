// Public presentation only. Never infer personal consent or alter signed terms.
export function stationFlow({station, loaded = false, unavailable = false} = {}) {
  const common = {
    title: "Charging approval",
    intro: "Check this station's rates, review your charging budget, then follow your sessions.",
    rows: [],
  };
  if (unavailable) return {...common, mode: "unavailable",
    note: "The station service is unavailable. Refresh this page after the operator restores it. No approval status can be checked here."};
  if (!loaded) return {...common, mode: "loading",
    note: "Checking the station's approval options. No spending approval is implied."};
  // Require an explicit boolean: malformed replies cannot enable an experiment.
  if (station?.monthly_enabled === true) return {...common, mode: "monthly",
    title: "Monthly charging",
    intro: "Check this station's rates, authorise monthly charging once, then follow your sessions.",
    note: ""};
  if (station?.monthly_enabled !== false) return stationFlow({unavailable: true});
  return {...common, mode: "weekly", title: "Weekly charging approval",
    intro: "Check the rates, authorise a weekly budget, then settle each charging session.",
    note: "Open the operator's invitation to authorise EV charging. Review its exact limit, covered sessions and expiry before signing. Sign-in alone does not approve spending.",
    rows: [
      ["Default invitation", "1,000 sat total for up to seven days, including network fees you pay."],
      ["Settlement", "One final net payment per completed session, not a weekly bill."],
      ["Credits", "Credits you receive do not refill the spending limit."],
      ["Renewal", "No automatic renewal. An expired or replaced approval needs a fresh signature."],
      ["Wallet", "Keep the session page and wallet available for collection. Wallet prompts may still need approval."],
    ]};
}
