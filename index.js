require('dotenv').config();

const jwt = require('jsonwebtoken');
const express = require('express');
const PQueue = require('p-queue').default;
const { checkVendorWithRetry } = require('./sanctionsService');
const { updateVendorRecord, getItemName } = require('./mondayService');

// Max 3 concurrent vendor checks — prevents flooding OpenSanctions during bulk imports
const vendorQueue = new PQueue({ concurrency: 3 });

const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());

// Returns decoded JWT payload (contains shortLivedToken) or null if invalid.
// In dev mode, falls back to personal MONDAY_API_TOKEN when no Authorization header.
function extractAuth(req) {
  const authHeader = req.headers['authorization'];

  if (!authHeader) {
    if (process.env.NODE_ENV !== 'production') {
      console.warn('[auth] No Authorization header — using MONDAY_API_TOKEN (dev mode)');
      return { shortLivedToken: process.env.MONDAY_API_TOKEN };
    }
    return null;
  }

  try {
    const token = authHeader.replace('Bearer ', '');
    const decoded = jwt.verify(token, process.env.MONDAY_SIGNING_SECRET);
    return decoded;
  } catch (err) {
    console.error('[auth] JWT verification failed:', err.message);
    return null;
  }
}

// Unwraps an inboundFieldValues entry that may be a primitive or an object
// wrapper (e.g. { columnId: "status" }). Returns the first matching key, or the
// value itself when it's already a primitive.
function fieldValue(field, ...keys) {
  if (field == null) return undefined;
  if (typeof field !== 'object') return field;
  for (const key of keys) {
    if (field[key] != null) return field[key];
  }
  return undefined;
}

// Health check — Monday Code / monitoring pings this
app.get('/', (req, res) => res.status(200).json({ status: 'ok' }));

// Automation Block action endpoint. Monday calls this when the automation's
// trigger fires ("When an item is created, screen it..."). The board and the
// columns are chosen by the CLIENT in the automation UI and arrive in the
// payload — NOT from our .env — so it works on any client board.
app.post('/monday/execute_action', async (req, res) => {
  // Monday URL verification challenge (sent when the action URL is registered)
  if (req.body.challenge) {
    return res.status(200).json({ challenge: req.body.challenge });
  }

  const auth = extractAuth(req);
  if (!auth) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  // New monday workflows infra sends `inboundFieldValues`; the older recipe
  // (sentence builder) infra used `inputFields`. Accept both for safety.
  const fields =
    req.body.payload?.inboundFieldValues || req.body.payload?.inputFields || {};

  // Column / board / item pickers arrive wrapped in an object
  // (e.g. { columnId: "status" }), not as a bare string. Unwrap to the id,
  // otherwise `{ [obj]: ... }` stringifies to "[object Object]" and Monday
  // rejects it with InvalidColumnIdException.
  const boardId = fieldValue(fields.boardId, 'boardId', 'id', 'value');
  const itemId = fieldValue(fields.itemId, 'itemId', 'linkedPulseId', 'id', 'value');
  const statusColumnId = fieldValue(fields.statusColumnId, 'columnId', 'id', 'value');
  const detailsColumnId = fieldValue(fields.detailsColumnId, 'columnId', 'id', 'value');
  // Per-account short-lived token from the JWT (dev: MONDAY_API_TOKEN)
  const apiToken = auth.shortLivedToken;

  if (!boardId || !itemId || !statusColumnId || !detailsColumnId) {
    return res.status(400).json({
      error: 'Missing required input fields (boardId, itemId, statusColumnId, detailsColumnId)',
    });
  }

  // Respond immediately — Monday times out if we wait for the full check
  res.status(200).json({});

  // Enqueue compliance check — max 3 concurrent to avoid rate limiting
  vendorQueue.add(() =>
    processVendor({ boardId, itemId, statusColumnId, detailsColumnId, apiToken })
  );
  console.log(`[queue] size=${vendorQueue.size} pending=${vendorQueue.pending}`);
});

async function processVendor({ boardId, itemId, statusColumnId, detailsColumnId, apiToken }) {
  try {
    const vendorName = await getItemName(itemId, apiToken);
    if (!vendorName) {
      console.error(`[vendor] Could not resolve name for item ${itemId} — skipping`);
      return;
    }

    console.log(`[vendor] Checking: "${vendorName}" (item ${itemId}, board ${boardId})`);

    const { riskLevel, details } = await checkVendorWithRetry(vendorName);

    console.log(`[vendor] Result for "${vendorName}": ${riskLevel}`);

    await updateVendorRecord({
      boardId,
      itemId,
      statusColumnId,
      detailsColumnId,
      riskLevel,
      details,
      apiToken,
    });

    console.log(`[vendor] Monday.com updated for item ${itemId}`);
  } catch (err) {
    console.error(`[vendor] Failed to process item ${itemId}:`, err.message);
  }
}

app.listen(PORT, () => {
  console.log(`VendorScreen AI listening on port ${PORT}`);
});
