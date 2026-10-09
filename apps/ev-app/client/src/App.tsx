import { DriverPage } from './ev/DriverPage.tsx'

// Read-only milestone: one driver page served by the integration at
// /bsv_settlement/app/. The scaffold's demo routes, BRC-103 login and signed
// requests were removed; the portal's own wallet-signature sign-in is used.
export default function App () {
  return <DriverPage />
}
