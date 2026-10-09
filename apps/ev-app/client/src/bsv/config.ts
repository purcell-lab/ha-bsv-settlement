// Centralized client configuration. Vite loads VITE_-prefixed vars from client/.env.
// Base URL of the server API. Defaults to the dev server; set VITE_API_URL in production
// (or whenever the client is served from a different origin than the API).
const configuredApiUrl = import.meta.env.VITE_API_URL
if (import.meta.env.PROD && configuredApiUrl == null) {
  throw new Error('VITE_API_URL is required in production')
}
const parsedApiUrl = new URL(configuredApiUrl ?? 'http://localhost:3000')
const localDevelopment = parsedApiUrl.protocol === 'http:' &&
  (parsedApiUrl.hostname === 'localhost' || parsedApiUrl.hostname === '127.0.0.1' || parsedApiUrl.hostname === '[::1]')
if ((parsedApiUrl.protocol !== 'https:' && !localDevelopment) || parsedApiUrl.username !== '' ||
    parsedApiUrl.password !== '' || parsedApiUrl.search !== '' || parsedApiUrl.hash !== '') {
  throw new Error('VITE_API_URL must be credential-free HTTPS (or exact HTTP localhost development)')
}
export const API_BASE_URL = parsedApiUrl.href.replace(/\/$/, '')
export const API_ORIGIN = parsedApiUrl.origin

// The scaffolded network default is concrete and can be overridden per deployment.
const configuredNetwork = import.meta.env.VITE_BSV_NETWORK ?? 'test'
if (configuredNetwork !== 'main' && configuredNetwork !== 'test' && configuredNetwork !== 'ttn') {
  throw new Error('VITE_BSV_NETWORK must be main, test, or ttn')
}
export const BSV_NETWORK = configuredNetwork
