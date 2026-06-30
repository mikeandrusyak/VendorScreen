require('dotenv').config();

const express = require('express');
const { mondaySdk } = require('@mondaycom/apps-sdk');
const { checkVendorWithRetry } = require('./sanctionsService');
const { updateVendorRecord } = require('./mondayService');

const app = express();
const monday = mondaySdk();
const PORT = process.env.PORT || 3000;

app.use(express.json());

app.post('/webhook', async (req, res) => {
  // Monday.com URL verification challenge (required during app setup)
  if (req.body.challenge) {
    return res.status(200).json({ challenge: req.body.challenge });
  }

  // Verify webhook signature to reject forged requests
  try {
    monday.verifyToken(req.headers['x-monday-signature'], req.body);
  } catch {
    return res.status(401).json({ error: 'Invalid signature' });
  }

  const { event } = req.body;
  const itemId = event?.pulseId;
  const vendorName = event?.pulseName;

  if (!itemId || !vendorName) {
    return res.status(400).json({ error: 'Missing itemId or vendorName in payload' });
  }

  // Respond immediately — Monday.com times out if we wait for the full check
  res.status(200).json({ status: 'received' });

  // Async compliance check runs after response is sent
  processVendor(itemId, vendorName);
});

async function processVendor(itemId, vendorName) {
  try {
    console.log(`[vendor] Checking: "${vendorName}" (item ${itemId})`);

    const { riskLevel, details } = await checkVendorWithRetry(vendorName);

    console.log(`[vendor] Result for "${vendorName}": ${riskLevel}`);

    await updateVendorRecord(itemId, riskLevel, details);

    console.log(`[vendor] Monday.com updated for item ${itemId}`);
  } catch (err) {
    console.error(`[vendor] Failed to process item ${itemId}:`, err.message);
  }
}

app.listen(PORT, () => {
  console.log(`VendorScreen AI listening on port ${PORT}`);
});
