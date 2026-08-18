const { Pool } = require('pg');

const pool = new Pool({
  connectionString: 'postgres://postgres:postgres@postgres:5432/logintel'
});

async function provisionSystem(clusterId, systemName) {
  const osUrl = 'http://opensearch:9200';
  const dashboardsUrl = 'http://dashboards:5601/osd';

  const makeReq = async (url, options) => {
    try {
      const res = await fetch(url, options);
      if (!res.ok) {
        const text = await res.text();
        console.error(`Provisioning request failed: ${url} - Status ${res.status}: ${text}`);
        return null;
      }
      return await res.json();
    } catch (err) {
      console.error(`Provisioning network error for ${url}:`, err);
      return null;
    }
  };

  // 1. Create global Index Pattern in Dashboards
  await makeReq(`${dashboardsUrl}/api/saved_objects/index-pattern/ip-logintel-logs?overwrite=true`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'osd-xsrf': 'true' },
    body: JSON.stringify({
      attributes: { title: `logintel-logs-*`, timeFieldName: '@timestamp' }
    })
  });

  // 2. Create Anomaly Detector for this cluster
  const safeName = systemName.replace(/[^a-zA-Z0-9_-]/g, '-').substring(0, 50);
  const detectorPayload = {
    name: `detector-${safeName}`,
    description: `Anomaly detector for ${systemName}`,
    time_field: "@timestamp",
    indices: [`logintel-logs-*`],
    filter_query: {
      bool: {
        filter: [
          { term: { "system.id": clusterId } },
          { terms: { "log.level": ["ERROR", "FATAL", "CRITICAL"] } }
        ]
      }
    },
    feature_attributes: [{
      feature_name: "error_count",
      feature_enabled: true,
      aggregation_query: {
        error_count: {
          value_count: {
            field: "_id"
          }
        }
      }
    }],
    detection_interval: {
      period: {
        interval: 1,
        unit: "Minutes"
      }
    },
    window_delay: {
      period: {
        interval: 1,
        unit: "Minutes"
      }
    }
  };

  const createRes = await makeReq(`${osUrl}/_plugins/_anomaly_detection/detectors`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(detectorPayload)
  });

  if (createRes && createRes._id) {
    console.log(`Detector created with ID ${createRes._id}. Starting it now...`);
    await makeReq(`${osUrl}/_plugins/_anomaly_detection/detectors/${createRes._id}/_start`, {
      method: 'POST'
    });
    console.log('Detector started.');
  } else {
    console.log('Failed to create detector.');
  }
}

async function run() {
  const client = await pool.connect();
  try {
    const res = await client.query('SELECT id, name FROM systems');
    for (const row of res.rows) {
      console.log(`Provisioning system: ${row.name} (${row.id})`);
      await provisionSystem(row.id, row.name);
    }
  } finally {
    client.release();
    pool.end();
  }
}

run();
