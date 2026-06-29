import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import Modal from 'react-modal';
import './index.css';
import App from './App.tsx';

const rootEl = document.getElementById('root');
if (!rootEl) throw new Error('Root element #root not found');

// react-modal accessibility: hide the app behind the overlay when a modal opens.
Modal.setAppElement(rootEl);

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
