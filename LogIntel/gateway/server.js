const express = require('express');
const { createProxyMiddleware, responseInterceptor } = require('http-proxy-middleware');
const { Pool } = require('pg');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const cors = require('cors');

const app = express();
app.use(cors());

// The agent URL is provided via environment or defaults to the internal docker network name
const AGENT_URL = process.env.AGENT_URL || 'http://agent:8000';
const JWT_SECRET = process.env.JWT_SECRET || 'super-secret-key-change-in-prod';
const PORT = process.env.PORT || 3000;

const pool = new Pool({
  host: process.env.PGHOST || 'postgres',
  port: process.env.PGPORT || 5432,
  user: process.env.PGUSER || 'postgres',
  password: process.env.PGPASSWORD || 'postgres',
  database: process.env.PGDATABASE || 'logintel',
});

async function initDB() {
  const client = await pool.connect();
  try {
    await client.query(`
      CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        role VARCHAR(20) NOT NULL DEFAULT 'developer'
      );
      CREATE TABLE IF NOT EXISTS systems (
        id VARCHAR(100) PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        token VARCHAR(255) NOT NULL
      );
      CREATE TABLE IF NOT EXISTS user_systems (
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        system_id VARCHAR(100) REFERENCES systems(id) ON DELETE CASCADE,
        PRIMARY KEY (user_id, system_id)
      );
    `);
    // Seed admin if not exists
    const res = await client.query('SELECT * FROM users WHERE username = $1', ['admin']);
    if (res.rows.length === 0) {
      const hash = await bcrypt.hash('admin', 10);
      await client.query(
        'INSERT INTO users (username, password_hash, role) VALUES ($1, $2, $3)',
        ['admin', hash, 'admin']
      );
      console.log('Admin user seeded with default password (admin)');
    }
  } finally {
    client.release();
  }
}
initDB().catch(console.error);

app.use(express.json());

// ---------------------------------------------------------
// AUTH ROUTES
// ---------------------------------------------------------
app.post('/api/auth/login', async (req, res) => {
  const { username, password } = req.body;
  try {
    const result = await pool.query('SELECT * FROM users WHERE username = $1', [username]);
    if (result.rows.length === 0) return res.status(401).json({ detail: 'Invalid credentials' });
    
    const user = result.rows[0];
    const valid = await bcrypt.compare(password, user.password_hash);
    if (!valid) return res.status(401).json({ detail: 'Invalid credentials' });

    // Fetch assigned systems if developer
    let systems = [];
    if (user.role === 'developer') {
      const sysRes = await pool.query('SELECT system_id FROM user_systems WHERE user_id = $1', [user.id]);
      systems = sysRes.rows.map(r => r.system_id);
    }

    const token = jwt.sign(
      { id: user.id, username: user.username, role: user.role, systems },
      JWT_SECRET,
      { expiresIn: '24h' }
    );
    res.json({ token, role: user.role });
  } catch (err) {
    console.error(err);
    res.status(500).json({ detail: 'Internal Server Error' });
  }
});

// Middleware to protect routes
const requireAuth = (req, res, next) => {
  const authHeader = req.headers.authorization;
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ detail: 'Missing or invalid token' });
  }
  const token = authHeader.split(' ')[1];
  try {
    const decoded = jwt.verify(token, JWT_SECRET);
    req.user = decoded;
    next();
  } catch (err) {
    return res.status(401).json({ detail: 'Token expired or invalid' });
  }
};

const requireAdmin = (req, res, next) => {
  if (req.user.role !== 'admin') {
    return res.status(403).json({ detail: 'Forbidden: Admin only' });
  }
  next();
};

const requireIngestAuth = async (req, res, next) => {
  const authHeader = req.headers.authorization;
  console.log('Received Ingest Auth Header:', authHeader);
  if (!authHeader) {
    return res.status(401).json({ detail: 'Missing authorization header' });
  }
  
  let token = null;
  if (authHeader.startsWith('Bearer ')) {
    token = authHeader.split(' ')[1];
  } else if (authHeader.startsWith('Basic ')) {
    // Fluent Bit's HTTP_User / HTTP_Passwd sends Basic Auth.
    // The token is sent in the password field.
    const b64 = authHeader.split(' ')[1];
    const decoded = Buffer.from(b64, 'base64').toString('utf8');
    const parts = decoded.split(':');
    token = parts[1]; // password field
  } else {
    return res.status(401).json({ detail: 'Invalid authorization format' });
  }

  if (!token) {
    return res.status(401).json({ detail: 'Missing token' });
  }
  
  token = token.trim();
  console.log('Token after trim: "' + token + '"');

  try {
    const result = await pool.query('SELECT id FROM systems WHERE token = $1', [token]);
    console.log('Query result rows:', result.rows.length);
    if (result.rows.length === 0) {
      return res.status(401).json({ detail: 'Invalid ingestion token' });
    }
    // Inject system ID for any downstream needs
    req.systemId = result.rows[0].id;
    next();
  } catch (err) {
    console.error(err);
    res.status(500).json({ detail: 'Internal Error during auth' });
  }
};

app.put('/api/auth/password', requireAuth, async (req, res) => {
  const { oldPassword, newPassword } = req.body;
  if (!oldPassword || !newPassword) return res.status(400).json({ detail: 'Missing fields' });

  try {
    const result = await pool.query('SELECT password_hash FROM users WHERE id = $1', [req.user.id]);
    if (result.rows.length === 0) return res.status(404).json({ detail: 'User not found' });
    
    const valid = await bcrypt.compare(oldPassword, result.rows[0].password_hash);
    if (!valid) return res.status(401).json({ detail: 'Incorrect old password' });
    
    const hash = await bcrypt.hash(newPassword, 10);
    await pool.query('UPDATE users SET password_hash = $1 WHERE id = $2', [hash, req.user.id]);
    res.json({ success: true });
  } catch (err) {
    console.error(err);
    res.status(500).json({ detail: 'Internal Server Error' });
  }
});

// ---------------------------------------------------------
// ADMIN USER MANAGEMENT
// ---------------------------------------------------------
app.get('/api/admin/users', requireAuth, requireAdmin, async (req, res) => {
  try {
    const uRes = await pool.query('SELECT id, username, role FROM users');
    const users = uRes.rows;
    for (const u of users) {
      if (u.role === 'developer') {
        const sRes = await pool.query('SELECT system_id FROM user_systems WHERE user_id = $1', [u.id]);
        u.systems = sRes.rows.map(r => r.system_id);
      } else {
        u.systems = [];
      }
    }
    res.json({ users });
  } catch (err) {
    console.error(err);
    res.status(500).json({ detail: 'Internal Error' });
  }
});

app.post('/api/admin/users', requireAuth, requireAdmin, async (req, res) => {
  const { username, password, role, systems } = req.body;
  if (!username || !password) return res.status(400).json({ detail: 'Username and password required' });
  
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const hash = await bcrypt.hash(password, 10);
    const uRes = await client.query(
      'INSERT INTO users (username, password_hash, role) VALUES ($1, $2, $3) RETURNING id',
      [username, hash, role || 'developer']
    );
    const userId = uRes.rows[0].id;

    if (systems && Array.isArray(systems)) {
      for (const sys of systems) {
        await client.query(
          'INSERT INTO user_systems (user_id, system_id) VALUES ($1, $2)',
          [userId, sys]
        );
      }
    }
    await client.query('COMMIT');
    res.json({ success: true, userId });
  } catch (err) {
    await client.query('ROLLBACK');
    if (err.code === '23505') return res.status(400).json({ detail: 'Username exists' });
    console.error(err);
    res.status(500).json({ detail: 'Internal Error' });
  } finally {
    client.release();
  }
});

app.put('/api/admin/users/:id/systems', requireAuth, requireAdmin, async (req, res) => {
  const userId = req.params.id;
  const { systems } = req.body;
  if (!Array.isArray(systems)) return res.status(400).json({ detail: 'systems array required' });
  
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query('DELETE FROM user_systems WHERE user_id = $1', [userId]);
    for (const sys of systems) {
      await client.query(
        'INSERT INTO user_systems (user_id, system_id) VALUES ($1, $2)',
        [userId, sys]
      );
    }
    await client.query('COMMIT');
    res.json({ success: true });
  } catch (err) {
    await client.query('ROLLBACK');
    console.error(err);
    res.status(500).json({ detail: 'Internal Error' });
  } finally {
    client.release();
  }
});

app.delete('/api/admin/users/:id', requireAuth, requireAdmin, async (req, res) => {
  const userId = req.params.id;
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query('DELETE FROM users WHERE id = $1', [userId]);
    await client.query('COMMIT');
    res.json({ success: true });
  } catch (err) {
    await client.query('ROLLBACK');
    console.error(err);
    res.status(500).json({ detail: 'Internal Error' });
  } finally {
    client.release();
  }
});

// ---------------------------------------------------------
// ADMIN SYSTEM MANAGEMENT
// ---------------------------------------------------------

async function provisionSystem(clusterId) {
  const osUrl = process.env.OPENSEARCH_URL || 'http://opensearch:9200';
  const dashboardsUrl = process.env.DASHBOARDS_URL || 'http://dashboards:5601/osd';

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
  const detectorPayload = {
    name: `detector-${clusterId}`,
    description: `Anomaly detector for ${clusterId}`,
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
          value_count: { field: "system.id" }
        }
      }
    }],
    detection_interval: { period: { interval: 1, unit: "Minutes" } },
    window_delay: { period: { interval: 1, unit: "Minutes" } }
  };
  
  const createRes = await makeReq(`${osUrl}/_plugins/_anomaly_detection/detectors`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(detectorPayload)
  });

  if (createRes && createRes._id) {
    console.log(`Detector created with ID ${createRes._id}. Starting it now...`);
    await makeReq(`${osUrl}/_plugins/_anomaly_detection/detectors/${createRes._id}/_start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
  }

  console.log(`Successfully initiated auto-provisioning for system ${clusterId}`);
}
app.get('/api/admin/systems', requireAuth, requireAdmin, async (req, res) => {
  try {
    const sRes = await pool.query('SELECT id, name, token FROM systems');
    res.json({ systems: sRes.rows });
  } catch (err) {
    console.error(err);
    res.status(500).json({ detail: 'Internal Error' });
  }
});

app.post('/api/admin/systems', requireAuth, requireAdmin, async (req, res) => {
  const { name } = req.body;
  if (!name) return res.status(400).json({ detail: 'Name required' });
  
  try {
    const clusterId = 'cls-' + require('crypto').randomBytes(4).toString('hex');
    const token = 'logintel_tok_' + require('crypto').randomBytes(16).toString('hex');
    
    await pool.query(
      'INSERT INTO systems (id, name, token) VALUES ($1, $2, $3)',
      [clusterId, name, token]
    );

    // Auto-provision OpenSearch resources asynchronously
    provisionSystem(clusterId);

    res.json({ success: true, system: { id: clusterId, name, token } });
  } catch (err) {
    console.error(err);
    res.status(500).json({ detail: 'Internal Error' });
  }
});

app.delete('/api/admin/systems/:id', requireAuth, requireAdmin, async (req, res) => {
  const sysId = req.params.id;
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query('DELETE FROM systems WHERE id = $1', [sysId]);
    await client.query('COMMIT');
    res.json({ success: true });
  } catch (err) {
    await client.query('ROLLBACK');
    console.error(err);
    res.status(500).json({ detail: 'Internal Error' });
  } finally {
    client.release();
  }
});

// ---------------------------------------------------------
// PROXY TO PYTHON AGENT (WITH RBAC ENFORCEMENT)
// ---------------------------------------------------------

// We must apply requireAuth to everything that goes to the proxy.
// However, the proxy middleware handles body streaming differently.
// So we use it below.

const agentProxy = createProxyMiddleware({
  target: AGENT_URL,
  changeOrigin: true,
  selfHandleResponse: true,
  onProxyReq: (proxyReq, req, res) => {
    // If it's a POST/PUT, we need to restream the body since express.json() consumed it
    if (req.body && Object.keys(req.body).length > 0 && req.method !== 'GET') {
      const bodyData = JSON.stringify(req.body);
      proxyReq.setHeader('Content-Type', 'application/json');
      proxyReq.setHeader('Content-Length', Buffer.byteLength(bodyData));
      proxyReq.write(bodyData);
    }
    
    // RBAC check: developers cannot query investigations for systems they don't own
    if (req.user.role === 'developer' && req.method === 'POST' && req.path === '/api/investigations') {
      const requestedSystem = req.body?.system?.id;
      if (requestedSystem && !req.user.systems.includes(requestedSystem)) {
        // Technically we can't easily abort a proxyReq inside onProxyReq by sending a 403 response
        // without throwing an error, but this is a simplified defense.
        // A better check would be done before reaching the proxy middleware, which we'll do below.
      }
    }
  },
  onProxyRes: responseInterceptor(async (responseBuffer, proxyRes, req, res) => {
    // We only want to intercept and filter JSON responses
    if (proxyRes.headers['content-type'] && proxyRes.headers['content-type'].includes('application/json')) {
      try {
        let data = JSON.parse(responseBuffer.toString('utf8'));
        
        // RBAC: Filter systems returned by /api/systems
        if (req.path === '/api/systems' && req.user.role === 'developer') {
          if (data.systems) {
            data.systems = data.systems.filter(s => req.user.systems.includes(s.id));
          }
        }
        
        // RBAC: Filter recent investigations returned by GET /api/investigations
        if (req.path === '/api/investigations' && req.method === 'GET' && req.user.role === 'developer') {
          if (data.investigations) {
            data.investigations = data.investigations.filter(inv => {
              const sysId = inv.plan?.system_id || inv.system_id;
              return req.user.systems.includes(sysId);
            });
          }
        }
        
        return JSON.stringify(data);
      } catch (e) {
        // If it's NDJSON (streaming investigations), it won't be fully parsed here.
        // But for POST /api/investigations the proxy passes it through directly if selfHandleResponse wasn't returning string.
        // However, responseInterceptor buffers everything, breaking NDJSON streams!
      }
    }
    return responseBuffer;
  }),
});

// Since we need streaming to work for POST /api/investigations, we CANNOT use responseInterceptor for it.
const streamingAgentProxy = createProxyMiddleware({
  target: AGENT_URL,
  changeOrigin: true,
  onProxyReq: (proxyReq, req, res) => {
    if (req.body && Object.keys(req.body).length > 0 && req.method !== 'GET') {
      const bodyData = JSON.stringify(req.body);
      proxyReq.setHeader('Content-Type', 'application/json');
      proxyReq.setHeader('Content-Length', Buffer.byteLength(bodyData));
      proxyReq.write(bodyData);
    }
  }
});

// Pre-proxy middleware to block developers from querying unauthorized systems
app.post('/api/investigations', requireAuth, (req, res, next) => {
  if (req.user.role === 'developer') {
    const sys = req.body?.system?.id;
    if (sys && !req.user.systems.includes(sys)) {
      return res.status(403).json({ detail: 'Forbidden system' });
    }
  }
  next();
}, streamingAgentProxy);

app.get('/api/settings', requireAuth, requireAdmin, agentProxy);
app.put('/api/settings', requireAuth, requireAdmin, agentProxy);
app.post('/api/settings/test', requireAuth, requireAdmin, agentProxy);
app.get('/api/clusters', requireAuth, requireAdmin, agentProxy);

// ---------------------------------------------------------
// INGESTION PROXY (WITH TOKEN ENFORCEMENT)
// ---------------------------------------------------------

const opensearchProxy = createProxyMiddleware({
  target: process.env.OPENSEARCH_URL || 'http://opensearch:9200',
  changeOrigin: true
});

const prometheusProxy = createProxyMiddleware({
  target: process.env.PROMETHEUS_URL || 'http://prometheus:9090',
  changeOrigin: true
});

// Expose these endpoints securely
app.post('/_bulk', requireIngestAuth, opensearchProxy);
app.post('/api/v1/write', requireIngestAuth, prometheusProxy);

// All other API routes go through the intercepting proxy (which filters JSON)
app.use('/api', requireAuth, agentProxy);

app.listen(PORT, () => {
  console.log(`Gateway listening on port ${PORT}`);
});
