import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.jsx'
import { PreferencesProvider } from './preferences'
import { ToastProvider } from './toast'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <PreferencesProvider>
      <ToastProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ToastProvider>
    </PreferencesProvider>
  </StrictMode>,
)
