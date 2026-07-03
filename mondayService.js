const axios = require('axios');

const MONDAY_API_URL = 'https://api.monday.com/v2';

// Maps our risk levels to Monday.com status column index values
// Verify these indices match your board's status column configuration
const STATUS_INDEX = {
  Clear: 1,
  Warning: 0,
  Critical: 2,
};

async function updateVendorRecord(itemId, riskLevel, details, apiToken) {
  const boardId = process.env.MONDAY_BOARD_ID;
  const columnValues = JSON.stringify({
    [process.env.COLUMN_ID_STATUS]: { index: STATUS_INDEX[riskLevel] },
    [process.env.COLUMN_ID_DETAILS]: { text: details },
  });

  const query = `
    mutation {
      change_multiple_column_values(
        board_id: ${boardId},
        item_id: ${itemId},
        column_values: ${JSON.stringify(columnValues)}
      ) {
        id
      }
    }
  `;

  const response = await axios.post(
    MONDAY_API_URL,
    { query },
    {
      headers: {
        'Content-Type': 'application/json',
        Authorization: apiToken,
      },
      timeout: 10000,
    }
  );

  if (response.data.errors) {
    throw new Error(`Monday GraphQL error: ${JSON.stringify(response.data.errors)}`);
  }

  return response.data;
}

module.exports = { updateVendorRecord };
