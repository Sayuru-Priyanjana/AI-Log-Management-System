import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.jsx'
import { PreferencesProvider } from './preferences'
import { ToastProvider } from './toast'
import { InvestigationProvider } from './InvestigationContext.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <PreferencesProvider>
      <ToastProvider>
        <InvestigationProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </InvestigationProvider>
      </ToastProvider>
    </PreferencesProvider>
  </StrictMode>,
)
