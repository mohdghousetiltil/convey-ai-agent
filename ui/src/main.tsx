import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import React from 'react';
import App from './App.tsx';
import './index.css';

class RootErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; message: string }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, message: '' };
  }

  static getDerivedStateFromError(error: unknown) {
    return { hasError: true, message: error instanceof Error ? error.message : String(error) };
  }

  componentDidCatch(error: unknown) {
    // Surface the crash in terminal logs too.
    // eslint-disable-next-line no-console
    console.error('Root render crash:', error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ minHeight: '100vh', background: '#f8fafc', color: '#0f172a', padding: '24px', fontFamily: 'system-ui' }}>
          <h1 style={{ fontSize: '18px', fontWeight: 700, marginBottom: '8px' }}>UI crashed while rendering</h1>
          <p style={{ fontSize: '14px', opacity: 0.9 }}>Error: {this.state.message || 'Unknown error'}</p>
          <p style={{ fontSize: '12px', marginTop: '12px', opacity: 0.75 }}>Please share this message so we can fix it immediately.</p>
        </div>
      );
    }
    return this.props.children;
  }
}

if (typeof window !== 'undefined') {
  const port = window.location.port;
  const isDevRuntime = port === '3000' || port === '5173';
  document.documentElement.dataset.runtimeUi = isDevRuntime ? 'dev' : 'served';
  document.documentElement.style.setProperty('--app-font-scale', isDevRuntime ? '1' : '1.05');
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RootErrorBoundary>
      <App />
    </RootErrorBoundary>
  </StrictMode>,
);
