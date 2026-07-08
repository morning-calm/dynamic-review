import type Modal from 'react-modal';

/** The app-standard react-modal style (dark, centered). Pass a width for the odd
 * modal that needs more room (default 480px). */
export const modalStyle = (maxWidth = '480px', width = '90%'): Modal.Styles => ({
  overlay: { backgroundColor: 'rgba(0,0,0,0.6)', zIndex: 50 },
  content: {
    inset: '50% auto auto 50%',
    transform: 'translate(-50%,-50%)',
    maxWidth,
    width,
    background: '#111827',
    border: '1px solid #374151',
    borderRadius: '0.5rem',
    padding: '1rem',
    color: 'white',
    maxHeight: '85vh',
    overflow: 'auto',
  },
});

export const MODAL_STYLE: Modal.Styles = modalStyle();
