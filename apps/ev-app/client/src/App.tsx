import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Home } from './bsv/Home'
import { WalletLogin } from './bsv/WalletLogin'
import { SignedRequestDemo } from './bsv/SignedRequestDemo'

export default function App () {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/login" element={<WalletLogin />} />
        <Route path="/signed-demo" element={<SignedRequestDemo />} />
      </Routes>
    </BrowserRouter>
  )
}
