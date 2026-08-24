import React, { useState, useEffect, useRef } from 'react';
import MonitoringChart from './MonitoringChart';
import TimeframePicker from './TimeframePicker';

export default function ChartModal({ isOpen, onClose, title, currentData, services, unit, fetchComparisonData, currentStart, currentEnd }) {
  // Timeframe state
  const [modalStart, setModalStart] = useState(currentStart);
  const [modalEnd, setModalEnd] = useState(currentEnd);
  
  // Data states
  const [primaryData, setPrimaryData] = useState(currentData || []);
  const [compareData, setCompareData] = useState(null);
  
  // UI states
  const [loadingPrimary, setLoadingPrimary] = useState(false);
  const [loadingCompare, setLoadingCompare] = useState(false);
  const [compareMode, setCompareMode] = useState('none');
  const [customDate, setCustomDate] = useState('');
  const [compareView, setCompareView] = useState('side-by-side'); // 'side-by-side' or 'overlay'
  
  // Service selection state
  const [selectedServices, setSelectedServices] = useState(services || []);
  const [isServicesOpen, setIsServicesOpen] = useState(false);
  const [serviceFilter, setServiceFilter] = useState('');
  const dropdownRef = useRef(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsServicesOpen(false);
      }
    }
    if (isServicesOpen) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isServicesOpen]);

  // Sync initial props
  useEffect(() => {
    if (isOpen) {
      setPrimaryData(currentData || []);
      setModalStart(currentStart);
      setModalEnd(currentEnd);
      setSelectedServices(services || []);
      setCompareMode('none');
      setCustomDate('');
    }
  }, [isOpen, currentData, currentStart, currentEnd, services]);

  // Fetch Primary Data when Timeframe changes (only if it differs from props)
  useEffect(() => {
    if (!isOpen || !fetchComparisonData) return;
    if (modalStart === currentStart && modalEnd === currentEnd) return; // handled by props

    let canceled = false;
    setLoadingPrimary(true);
    fetchComparisonData(modalStart, modalEnd)
      .then(data => {
        if (!canceled) setPrimaryData(data || []);
      })
      .catch(err => console.error("Failed to fetch primary data", err))
      .finally(() => {
        if (!canceled) setLoadingPrimary(false);
      });
      
    return () => { canceled = true; };
  }, [modalStart, modalEnd, isOpen, fetchComparisonData, currentStart, currentEnd]);

  // Fetch Compare Data
  useEffect(() => {
    if (!isOpen || !fetchComparisonData || compareMode === 'none') {
      setCompareData(null);
      return;
    }

    let compStart, compEnd;
    const duration = modalEnd - modalStart;

    if (compareMode === 'yesterday') {
      compStart = modalStart - (24 * 3600);
      compEnd = modalEnd - (24 * 3600);
    } else if (compareMode === 'lastWeek') {
      compStart = modalStart - (7 * 24 * 3600);
      compEnd = modalEnd - (7 * 24 * 3600);
    } else if (compareMode === 'custom' && customDate) {
      const selectedDate = new Date(customDate);
      compStart = Math.floor(selectedDate.getTime() / 1000);
      compEnd = compStart + duration;
    } else {
      setCompareData(null);
      return;
    }

    let canceled = false;
    setLoadingCompare(true);
    fetchComparisonData(compStart, compEnd)
      .then(data => {
        if (!canceled) setCompareData(data || []);
      })
      .catch(err => console.error("Failed to fetch compare data", err))
      .finally(() => {
        if (!canceled) setLoadingCompare(false);
      });

    return () => { canceled = true; };
  }, [compareMode, customDate, modalStart, modalEnd, isOpen, fetchComparisonData]);


  if (!isOpen) return null;

  const handleServiceToggle = (svc) => {
    if (selectedServices.includes(svc)) {
      setSelectedServices(selectedServices.filter(s => s !== svc));
    } else {
      setSelectedServices([...selectedServices, svc]);
    }
  };

  const filteredServices = services.filter(svc => svc.toLowerCase().includes(serviceFilter.toLowerCase()));

  const selectAll = () => setSelectedServices(Array.from(new Set([...selectedServices, ...filteredServices])));
  const clearAll = () => setSelectedServices(selectedServices.filter(s => !filteredServices.includes(s)));

  // Prepare overlaid data if needed
  const getMergedOverlayData = () => {
    if (!compareData || compareData.length === 0) return primaryData;
    
    return primaryData.map((pt, i) => {
      const mergedPt = { ...pt };
      const compPt = compareData[i];
      if (compPt) {
        services.forEach(svc => {
          if (compPt[svc] !== undefined) {
            mergedPt[`${svc}_compare`] = compPt[svc];
          }
        });
      }
      return mergedPt;
    });
  };

  const isOverlay = compareMode !== 'none' && compareView === 'overlay' && compareData;

  const renderSingleChart = (dataToRender, chartTitle, isLoading, compareSvc = []) => (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      <MonitoringChart 
        title={chartTitle}
        data={dataToRender}
        services={selectedServices}
        compareServices={compareSvc}
        loading={isLoading}
        unit={unit}
        defaultTopN="all"
      />
    </div>
  );

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 9999, backgroundColor: 'rgba(0,0,0,0.85)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '40px' }}>
      <div style={{ background: 'var(--bg)', width: '100%', height: '100%', maxWidth: '1600px', maxHeight: '900px', borderRadius: '8px', display: 'flex', flexDirection: 'column', boxShadow: '0 10px 40px rgba(0,0,0,0.8)', border: '1px solid var(--border)' }}>
        
        {/* Header */}
        <header style={{ padding: '20px 24px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '16px' }}>
          <h2 style={{ margin: 0, color: 'var(--text)', fontSize: '18px' }}>{title}</h2>
          
          <div style={{ display: 'flex', alignItems: 'center', gap: '20px', flexWrap: 'wrap' }}>
            
            {/* Service Multi-Select */}
            <div style={{ position: 'relative' }} ref={dropdownRef}>
              <button 
                className="btn btn--sm" 
                onClick={() => setIsServicesOpen(!isServicesOpen)}
                style={{ backgroundColor: 'var(--surface)', border: '1px solid var(--border)' }}
              >
                Services ({selectedServices.length}/{services.length})
              </button>
              {isServicesOpen && (
                <div style={{ position: 'absolute', top: '100%', left: 0, marginTop: '4px', backgroundColor: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '4px', padding: '8px', zIndex: 10, width: '260px', maxHeight: '400px', overflowY: 'auto', boxShadow: '0 4px 12px rgba(0,0,0,0.5)' }}>
                  <div style={{ marginBottom: '8px' }}>
                    <input 
                      type="text" 
                      placeholder="Search services..." 
                      className="input input--sm" 
                      value={serviceFilter}
                      onChange={e => setServiceFilter(e.target.value)}
                      style={{ width: '100%', backgroundColor: 'var(--bg)', marginBottom: '8px' }}
                    />
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px', borderBottom: '1px solid var(--border)', paddingBottom: '8px' }}>
                    <button className="btn btn--sm" style={{ padding: '2px 8px', fontSize: '11px' }} onClick={selectAll}>Select All</button>
                    <button className="btn btn--sm" style={{ padding: '2px 8px', fontSize: '11px' }} onClick={clearAll}>Clear</button>
                  </div>
                  {filteredServices.length === 0 && (
                    <div style={{ padding: '8px', fontSize: '12px', color: 'var(--text-3)', textAlign: 'center' }}>No matches</div>
                  )}
                  {filteredServices.map(svc => (
                    <label key={svc} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '4px 0', fontSize: '13px', cursor: 'pointer' }}>
                      <input 
                        type="checkbox" 
                        checked={selectedServices.includes(svc)}
                        onChange={() => handleServiceToggle(svc)}
                      />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{svc}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            {/* Timeframe Picker */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-3)' }}>Time:</span>
              <TimeframePicker 
                defaultLabel="Custom"
                onChange={(tf) => {
                  setModalStart(tf.start);
                  setModalEnd(tf.end);
                }} 
              />
            </div>

            {/* Compare Tools */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', borderLeft: '1px solid var(--border)', paddingLeft: '20px' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-3)' }}>Compare:</span>
              <select 
                className="input input--sm" 
                value={compareMode} 
                onChange={e => setCompareMode(e.target.value)}
                style={{ backgroundColor: 'var(--surface)', width: 'auto' }}
              >
                <option value="none">None</option>
                <option value="yesterday">Yesterday</option>
                <option value="lastWeek">Last Week</option>
                <option value="custom">Custom Date...</option>
              </select>

              {compareMode === 'custom' && (
                <input 
                  type="date" 
                  className="input input--sm" 
                  value={customDate} 
                  onChange={(e) => setCustomDate(e.target.value)}
                  style={{ backgroundColor: 'var(--surface)' }}
                />
              )}

              {compareMode !== 'none' && (
                <div style={{ display: 'flex', backgroundColor: 'var(--bg)', borderRadius: '4px', overflow: 'hidden', border: '1px solid var(--border)' }}>
                  <button 
                    className="btn btn--sm" 
                    style={{ border: 'none', borderRadius: 0, backgroundColor: compareView === 'side-by-side' ? 'var(--primary)' : 'transparent', color: compareView === 'side-by-side' ? '#fff' : 'var(--text)' }}
                    onClick={() => setCompareView('side-by-side')}
                  >
                    Side-by-Side
                  </button>
                  <button 
                    className="btn btn--sm" 
                    style={{ border: 'none', borderRadius: 0, backgroundColor: compareView === 'overlay' ? 'var(--primary)' : 'transparent', color: compareView === 'overlay' ? '#fff' : 'var(--text)' }}
                    onClick={() => setCompareView('overlay')}
                  >
                    Overlay
                  </button>
                </div>
              )}
            </div>

            <button className="btn btn--sm btn--primary" onClick={onClose} style={{ marginLeft: 'auto' }}>Close</button>
          </div>
        </header>

        {/* Body */}
        <div style={{ flex: 1, padding: '24px', display: 'flex', gap: '24px', minHeight: 0 }}>
          {isOverlay ? (
            renderSingleChart(
              getMergedOverlayData(), 
              'Overlay Comparison (Dashed lines = Comparison)', 
              loadingPrimary || loadingCompare, 
              selectedServices.map(s => `${s}_compare`)
            )
          ) : (
            <>
              {renderSingleChart(primaryData, 'Current Timeframe', loadingPrimary)}
              
              {compareMode !== 'none' && (
                renderSingleChart(compareData, `Comparison Data`, loadingCompare)
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
