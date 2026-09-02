import { useState } from 'react';

export default function FeedbackModal({ onClose }) {
  const [feedback, setFeedback] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!feedback.trim()) return;
    setSubmitting(true);
    setStatus(null);
    try {
      const response = await fetch("https://default099ec11549494b43b2e571707cbb16.b4.environment.api.powerplatform.com:443/powerautomate/automations/direct/cu/10/workflows/e7930ce8804e457cb950df1820115fd4/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=ugTDvyn80QNdExCTLmuisz_GN4UCYHs2-HHmFHV-7Tc", {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          text: feedback, 
          user: localStorage.getItem('username') || 'Anonymous' 
        })
      });
      if (response.ok || response.status === 202) {
        setStatus('success');
        setTimeout(onClose, 2000);
      } else {
        setStatus('error');
      }
    } catch (err) {
      setStatus('error');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Send Feedback to Teams</h2>
          <button className="iconbtn" onClick={onClose}>&times;</button>
        </div>
        <div className="modal-body" style={{ padding: '1.5rem' }}>
          {status === 'success' ? (
             <div className="alert is-success">Feedback sent successfully! Thank you.</div>
          ) : (
            <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                <label style={{ fontWeight: '500' }}>Your Message</label>
                <textarea 
                  rows="5"
                  value={feedback}
                  onChange={e => setFeedback(e.target.value)}
                  placeholder="Found a bug? Have a suggestion? Let us know!"
                  required
                  style={{ padding: '0.75rem', borderRadius: '4px', border: '1px solid var(--border)', background: 'var(--bg-inset)', color: 'var(--text)' }}
                />
              </div>
              
              {status === 'error' && (
                <div style={{ padding: '0.75rem', background: 'rgba(239, 68, 68, 0.1)', color: '#ef4444', borderRadius: '4px', fontSize: '0.9rem' }}>
                  Failed to send feedback. Please try again later.
                </div>
              )}
              
              <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end', marginTop: '0.5rem' }}>
                <button type="button" className="btn btn-secondary" onClick={onClose} disabled={submitting}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={submitting || !feedback.trim()}>
                  {submitting ? 'Sending...' : 'Send Feedback'}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
