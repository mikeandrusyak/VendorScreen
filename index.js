require('dotenv').config();

const jwt = require('jsonwebtoken');
const express = require('express');
const { checkVendorWithRetry } = require('./sanctionsService');
const { updateVendorRecord } = require('./mondayService');

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

app.post('/webhook', async (req, res) => {
  // Monday.com URL verification challenge (required during app setup)
  if (req.body.challenge) {
    return res.status(200).json({ challenge: req.body.challenge });
  }

  const auth = extractAuth(req);
  if (!auth) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  const { event } = req.body;
  const itemId = event?.pulseId;
  const vendorName = event?.pulseName;
  // In production: shortLivedToken from JWT (5-min validity, per-account)
  // In dev: personal MONDAY_API_TOKEN from .env
  const apiToken = auth.shortLivedToken;

  if (!itemId || !vendorName) {
    return res.status(400).json({ error: 'Missing itemId or vendorName in payload' });
  }

  // Respond immediately — Monday.com times out if we wait for the full check
  res.status(200).json({ status: 'received' });

  // Async compliance check runs after response is sent
  processVendor(itemId, vendorName, apiToken);
});

async function processVendor(itemId, vendorName, apiToken) {
  try {
    console.log(`[vendor] Checking: "${vendorName}" (item ${itemId})`);

    const { riskLevel, details } = await checkVendorWithRetry(vendorName);

    console.log(`[vendor] Result for "${vendorName}": ${riskLevel}`);

    await updateVendorRecord(itemId, riskLevel, details, apiToken);

    console.log(`[vendor] Monday.com updated for item ${itemId}`);
  } catch (err) {
    console.error(`[vendor] Failed to process item ${itemId}:`, err.message);
  }
}

app.listen(PORT, () => {
  console.log(`VendorScreen AI listening on port ${PORT}`);
});
