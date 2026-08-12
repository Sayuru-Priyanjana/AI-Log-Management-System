import { useState } from 'react';
import { changePassword } from '../api';
import { useToast } from '../toast';

export default function ChangePasswordModal({ onClose }) {
  const toast = useToast();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    if (next !== confirm) return toast.error('The new passwords do not match');
    if (next.length < 5) return toast.error('Password must be at least 5 characters');

    setBusy(true);
    try {
      await changePassword(current, next);
      // Closing immediately is the confirmation. The toast survives the modal,
      // which is why it can be dismissed the moment the work is done rather
      // than holding the dialog open for two seconds to display a tick.
      toast.success('Password updated');
      onClose();
    } catch (err) {
      toast.error('Could not change password', { detail: err.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Change password">
        <header>
          <h3>Change password</h3>
          <button type="button" className="iconbtn" onClick={onClose} aria-label="Close">×</button>
        </header>
        <form onSubmit={submit}>
          <div className="modal-body">
            <div className="field">
              <label htmlFor="pw-current">Current password</label>
              <input id="pw-current" className="input" type="password" value={current} autoFocus
                autoComplete="current-password" onChange={(e) => setCurrent(e.target.value)} required />
            </div>
            <div className="field">
              <label htmlFor="pw-new">New password</label>
              <input id="pw-new" className="input" type="password" value={next}
                autoComplete="new-password" onChange={(e) => setNext(e.target.value)} required />
            </div>
            <div className="field">
              <label htmlFor="pw-confirm">Confirm new password</label>
              <input id="pw-confirm" className="input" type="password" value={confirm}
                autoComplete="new-password" onChange={(e) => setConfirm(e.target.value)} required />
            </div>
          </div>
          <div className="modal-foot">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn--primary" disabled={busy}>
              {busy ? 'Updating…' : 'Update'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
