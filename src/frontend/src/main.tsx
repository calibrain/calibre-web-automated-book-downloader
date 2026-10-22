import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { App } from './App';
import { SocketProvider } from './contexts/SocketContext';
import { locale } from './i18n';
import { getBasePath } from './utils/basePath';

document.documentElement.lang = locale;

const root = document.getElementById('root');
if (!root) throw new Error('Root element not found');

const basePath = getBasePath();
const routerBase = basePath === '/' ? undefined : basePath;

createRoot(root).render(
  <StrictMode>
    <BrowserRouter basename={routerBase}>
      <SocketProvider>
        <App />
      </SocketProvider>
    </BrowserRouter>
  </StrictMode>,
);
