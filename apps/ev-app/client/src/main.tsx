import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { WalletProviders } from './bsv/WalletProviders'
import './ev/ev.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <WalletProviders>
      <App />
    </WalletProviders>
  </StrictMode>
)
