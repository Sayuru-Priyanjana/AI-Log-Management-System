import { useState } from 'react';

export default function FeedbackModal({ onClose }) {
  const [feedback, setFeedback] = useState('');
  const [category, setCategory] = useState('General Feedback');
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!feedback.trim()) return;
    setSubmitting(true);
    setStatus(null);
    
    const username = localStorage.getItem('username') || 'Anonymous';
    const timestamp = new Date().toLocaleString();

    try {
      const response = await fetch("https://default099ec11549494b43b2e571707cbb16.b4.environment.api.powerplatform.com:443/powerautomate/automations/direct/cu/10/workflows/e7930ce8804e457cb950df1820115fd4/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=ugTDvyn80QNdExCTLmuisz_GN4UCYHs2-HHmFHV-7Tc", {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: "message",
          attachments: [
            {
              contentType: "application/vnd.microsoft.card.adaptive",
              content: {
                $schema: "http://adaptivecards.io/schemas/adaptive-card.json",
                type: "AdaptiveCard",
                version: "1.4",
                body: [
                  {
                    type: "Container",
                    style: "emphasis",
                    padding: "10px",
                    items: [
                      {
                        type: "TextBlock",
                        text: "🔔 New System Feedback",
                        weight: "Bolder",
                        size: "Large",
                        color: "Accent"
                      }
                    ]
                  },
                  {
                    type: "FactSet",
                    spacing: "Medium",
                    facts: [
                      { title: "Category:", value: category },
                      { title: "Submitted By:", value: `**${username}**` },
                      { title: "Time:", value: timestamp }
                    ]
                  },
                  {
                    type: "TextBlock",
                    text: "**Message:**",
                    spacing: "Medium",
                    size: "Medium"
                  },
                  {
                    type: "TextBlock",
                    text: feedback,
                    wrap: true
                  }
                ]
              }
            }
          ]
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
        <header>
          <h3 style={{ margin: 0 }}>Send Feedback</h3>
          <button type="button" className="iconbtn" onClick={onClose} title="Close">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </header>

        {status === 'success' ? (
          <div className="modal-body" style={{ padding: '24px', textAlign: 'center' }}>
            <div style={{ color: 'var(--success)', marginBottom: '12px' }}>
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                <polyline points="22 4 12 14.01 9 11.01"></polyline>
              </svg>
            </div>
            <h3 style={{ marginBottom: '8px' }}>Thank you!</h3>
            <p style={{ color: 'var(--text-muted)' }}>Your feedback has been sent directly to the Teams channel.</p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', margin: 0 }}>
            <div className="modal-body" style={{ padding: '16px' }}>
              <p style={{ margin: '0 0 12px 0', fontSize: '0.9rem', color: 'var(--text-muted)' }}>
                Your feedback is essential for maintaining and enhancing system stability and functionality. Please outline your observations or requests below.
              </p>
              
              <div style={{ marginBottom: '12px' }}>
                <label style={{ display: 'block', marginBottom: '6px', fontSize: '0.9rem', fontWeight: 500 }}>Category</label>
                <select 
                  className="input" 
                  value={category} 
                  onChange={e => setCategory(e.target.value)}
                  style={{ width: '100%' }}
                >
                  <option value="Bug Report">Bug Report</option>
                  <option value="Feature Request">Feature Request</option>
                  <option value="General Feedback">General Feedback</option>
                  <option value="Question / Help">Question / Help</option>
                </select>
              </div>

              <div>
                <label style={{ display: 'block', marginBottom: '6px', fontSize: '0.9rem', fontWeight: 500 }}>Message</label>
                <textarea 
                  rows="5"
                  value={feedback}
                  onChange={e => setFeedback(e.target.value)}
                  placeholder="Describe the issue or suggestion..."
                  required
                  className="input"
                  style={{ resize: 'vertical', width: '100%', minHeight: '100px' }}
                />
              </div>
              
              {status === 'error' && (
                <div style={{ marginTop: '12px', padding: '10px', background: 'rgba(239, 68, 68, 0.1)', borderLeft: '3px solid #ef4444', color: '#ef4444', borderRadius: '4px', fontSize: '0.85rem' }}>
                  Failed to send feedback. Please try again later.
                </div>
              )}
            </div>
            
            <footer className="modal-foot">
              <button type="button" className="btn" onClick={onClose} disabled={submitting}>
                Cancel
              </button>
              <button type="submit" className="btn is-primary" disabled={submitting || !feedback.trim()}>
                {submitting ? 'Sending...' : 'Send Feedback'}
              </button>
            </footer>
          </form>
        )}
      </div>
    </div>
  );
}
