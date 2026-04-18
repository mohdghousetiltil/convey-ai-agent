import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App.tsx';
import './index.css';

if (typeof window !== 'undefined') {
  const port = window.location.port;
  const isDevRuntime = port === '3000' || port === '5173';
  document.documentElement.dataset.runtimeUi = isDevRuntime ? 'dev' : 'served';
  document.documentElement.style.setProperty('--app-font-scale', isDevRuntime ? '1' : '1.05');
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
