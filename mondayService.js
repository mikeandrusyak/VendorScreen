const axios = require('axios');

const MONDAY_API_URL = 'https://api.monday.com/v2';

async function mondayRequest(query, variables, apiToken) {
  const response = await axios.post(
    MONDAY_API_URL,
    { query, variables },
    {
      headers: {
        'Content-Type': 'application/json',
        Authorization: apiToken,
        'API-Version': '2024-01',
      },
      timeout: 10000,
    }
  );

  if (response.data.errors) {
    throw new Error(`Monday GraphQL error: ${JSON.stringify(response.data.errors)}`);
  }

  return response.data;
}

// Fetches the item's name (vendor name) so we don't depend on the trigger
// output mapping — works on any client board.
async function getItemName(itemId, apiToken) {
  const query = `query ($itemId: [ID!]) { items (ids: $itemId) { name } }`;
  const data = await mondayRequest(query, { itemId: [String(itemId)] }, apiToken);
  return data.data?.items?.[0]?.name || null;
}

// Writes the risk result to the columns the CLIENT mapped in the recipe.
// Status is written by LABEL (not index): Monday resolves the label to the
// correct index on any board regardless of order, so it works on client boards
// whose status columns differ from ours. Labels must match the status column
// options ("Clear" / "Warning" / "Critical").
async function updateVendorRecord({
  boardId,
  itemId,
  statusColumnId,
  detailsColumnId,
  riskLevel,
  details,
  apiToken,
}) {
  const columnValues = JSON.stringify({
    [statusColumnId]: { label: riskLevel },
    [detailsColumnId]: details,
  });

  const query = `
    mutation ($boardId: ID!, $itemId: ID!, $columnValues: JSON!) {
      change_multiple_column_values(
        board_id: $boardId,
        item_id: $itemId,
        column_values: $columnValues,
        create_labels_if_missing: true
      ) {
        id
      }
    }
  `;

  return mondayRequest(
    query,
    {
      boardId: String(boardId),
      itemId: String(itemId),
      columnValues,
    },
    apiToken
  );
}

module.exports = { updateVendorRecord, getItemName };
