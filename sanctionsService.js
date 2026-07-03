const axios = require('axios');

const OPENSANCTIONS_BASE_URL = 'https://api.opensanctions.org';

// Risk level constants matching Monday.com status column labels
const RISK_LEVEL = {
  CLEAR: 'Clear',
  WARNING: 'Warning',
  CRITICAL: 'Critical',
};

// Appended to every details string written to the board. VendorScreen AI is an
// informational screening tool — it does not make compliance decisions. See
// TERMS_OF_SERVICE.md §2. This disclaimer must remain visible in the output.
const DISCLAIMER =
  ' — Informational screening only, not a compliance decision. Results may be ' +
  'incomplete or inaccurate; verify independently before acting.';

function withDisclaimer(details) {
  return `${details}${DISCLAIMER}`;
}

async function checkVendor(vendorName) {
  const response = await axios.get(`${OPENSANCTIONS_BASE_URL}/search/default`, {
    params: { q: vendorName, limit: 5 },
    headers: { Authorization: `ApiKey ${process.env.OPENSANCTIONS_API_KEY}` },
    timeout: 10000,
  });

  const results = response.data.results || [];

  if (results.length === 0) {
    return {
      riskLevel: RISK_LEVEL.CLEAR,
      details: withDisclaimer('No matches found in OpenSanctions.'),
    };
  }

  // Check for active sanctions first (Critical), then PEP/minor flags (Warning)
  const hasCritical = results.some((entity) =>
    (entity.datasets || []).some((ds) => ds.includes('sanctions'))
  );

  if (hasCritical) {
    const match = results[0];
    const profileUrl = `https://www.opensanctions.org/entities/${match.id}/`;
    return {
      riskLevel: RISK_LEVEL.CRITICAL,
      details: withDisclaimer(`Possible direct sanction match: ${match.caption}. Profile: ${profileUrl}`),
    };
  }

  const hasWarning = results.some((entity) => {
    const datasets = entity.datasets || [];
    const properties = entity.properties || {};
    return (
      datasets.some((ds) => ds.includes('pep')) ||
      (properties.topics || []).includes('poi')
    );
  });

  if (hasWarning) {
    const match = results[0];
    const profileUrl = `https://www.opensanctions.org/entities/${match.id}/`;
    return {
      riskLevel: RISK_LEVEL.WARNING,
      details: withDisclaimer(`Possible PEP or minor flag: ${match.caption}. Profile: ${profileUrl}`),
    };
  }

  return {
    riskLevel: RISK_LEVEL.CLEAR,
    details: withDisclaimer(`No active sanctions or PEP flags. Possible non-critical match: ${results[0].caption}.`),
  };
}

// Wraps checkVendor with retry logic for 429 rate limiting
async function checkVendorWithRetry(vendorName, retries = 3, delayMs = 2000) {
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      return await checkVendor(vendorName);
    } catch (err) {
      const status = err.response?.status;
      if (status === 429 && attempt < retries) {
        const retryAfter = parseInt(err.response.headers['retry-after'] || '0', 10);
        const waitMs = retryAfter ? retryAfter * 1000 : delayMs * attempt;
        console.warn(`[sanctions] Rate limited. Retrying in ${waitMs}ms (attempt ${attempt}/${retries})`);
        await new Promise((resolve) => setTimeout(resolve, waitMs));
      } else {
        throw err;
      }
    }
  }
}

module.exports = { checkVendorWithRetry, RISK_LEVEL };
