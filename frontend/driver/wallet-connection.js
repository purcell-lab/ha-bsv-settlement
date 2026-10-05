export function walletConnectionUnavailable(error) {
  return /no wallet available|wallet apis are unavailable|web2 mode|wallet connection timed out/i.test(error?.message||String(error));
}
export function walletConnectionHelp(enrolment=false) {
  return "This page cannot reach a BSV wallet. Your budget is not approved yet. " +
    "On this phone, copy this page link and open it inside BSV Browser in wallet-enabled mode, then authorise again." +
    (enrolment?"":" On another screen, use Connect BSV Browser to pair first.");
}
